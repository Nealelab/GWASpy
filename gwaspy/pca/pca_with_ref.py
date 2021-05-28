__author__ = 'Lindo Nkambule'

import hail as hl
import pandas as pd
from gwaspy.pca.pca_filter_snps import pca_filter_mt
import random
from sklearn.ensemble import RandomForestClassifier
from typing import Tuple
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.backends.backend_pdf import PdfPages


def pc_project(
        mt: hl.MatrixTable = None,
        loadings_ht: hl.Table = None,
        loading_location: str = 'loadings',
        af_location: str = 'pca_af') -> hl.Table:
    """
    Projects samples in `mt` on pre-computed PCs.

    :param mt: MT containing the samples to project
    :param loadings_ht: HT containing the PCA loadings and allele frequencies used for the PCA
    :param loading_location: Location of expression for loadings in `loadings_ht`
    :param af_location: Location of expression for allele frequency in `loadings_ht`
    :return: Table with scores calculated from loadings in column `scores`
    """

    n_variants = loadings_ht.count()

    mt = mt.annotate_rows(
        pca_loadings=loadings_ht[mt.row_key][loading_location],
        pca_af=loadings_ht[mt.row_key][af_location],
    )

    mt = mt.filter_rows(
        hl.is_defined(mt.pca_loadings)
        & hl.is_defined(mt.pca_af)
        & (mt.pca_af > 0)
        & (mt.pca_af < 1)
    )

    gt_norm = (mt.GT.n_alt_alleles() - 2 * mt.pca_af) / hl.sqrt(
        n_variants * 2 * mt.pca_af * (1 - mt.pca_af)
    )

    mt = mt.annotate_cols(scores=hl.agg.array_sum(mt.pca_loadings * gt_norm))

    return mt.cols().select('scores')


def intersect_ref(
        ref_dirname: str = 'gs://african-seq-data/hgdp_tgp/gwaspy_pca_ref/',
        ref_basename: str = 'hgdp_1kg_filtered_maf_5_GRCh38',
        data_mt: hl.MatrixTable = None,
        data_basename: str = None, out_dir: str = None):
    """
    Intersects reference panel with the data and writes intersections as matrix tables
    :param ref_dirname: directory name where reference data is
    :param ref_basename: base filename for reference data
    :param data_mt: input data MatrixTable
    :param data_basename: base filename for input data
    :param out_dir: output directory where files are going to be saved to
    :return:
    """
    print('Reading reference data mt')
    ref_mt = hl.read_matrix_table(ref_dirname + ref_basename + '.mt')

    # filter data to sites in ref & array data
    data_in_ref = data_mt.filter_rows(hl.is_defined(ref_mt.rows()[data_mt.row_key]))
    print('\nsites in ref and data, inds in data: {}'.format(data_in_ref.count()))
    data_in_ref.write(out_dir + 'GWASpy/PCA/' + data_basename + '_intersect_1000G.mt', overwrite=True)

    # filter ref to data sites
    ref_in_data = ref_mt.filter_rows(hl.is_defined(data_mt.rows()[ref_mt.row_key]))
    print('\nsites in ref and data, inds in ref: {}'.format(ref_in_data.count()))  #
    ref_in_data.write(out_dir + 'GWASpy/PCA/' + '1000G_intersect_' + data_basename + '.mt', overwrite=True)


def run_ref_pca(
        mt: hl.MatrixTable,
        out_dir: str):
    """
    Run PCA on a dataset
    :param mt: dataset to run PCA on
    :param out_dir: directory and filename prefix for where to put PCA output
    :return:
    """
    pca_evals, pca_scores, pca_loadings = hl.hwe_normalized_pca(mt.GT, k=20, compute_loadings=True)
    pca_mt = mt.annotate_rows(pca_af=hl.agg.mean(mt.GT.n_alt_alleles()) / 2)
    pca_loadings = pca_loadings.annotate(pca_af=pca_mt.rows()[pca_loadings.key].pca_af)

    pca_scores.write(out_dir + 'GWASpy/PCA/' + '1000G_scores.ht', overwrite=True)
    pca_scores = hl.read_table(out_dir + 'GWASpy/PCA/' + '1000G_scores.ht')
    pca_scores = pca_scores.transmute(**{f'PC{i}': pca_scores.scores[i - 1] for i in range(1, 21)})
    pca_scores.export(out_dir + 'GWASpy/PCA/' + '1000G_scores.txt.bgz')  # individual-level PCs

    pca_loadings.write(out_dir + 'GWASpy/PCA/' + '1000G_loadings.ht', overwrite=True)  # PCA loadings


def merge_data_with_ref(
        ref_scores: str = None,
        ref_info: str = None,
        data_scores: str = None) -> pd.DataFrame:
    """
    Merge data with ref
    :param ref_scores: path to reference score
    :param ref_info: path to information about samples in the ref scores
    :param data_scores: path to input data scores
    :return: a pandas Dataframe of data merged with reference
    """

    print('\nMerging data with ref')
    ref = pd.read_table(ref_scores, header=0, sep='\t', compression='gzip')
    data = pd.read_table(data_scores, header=0, sep='\t')
    ref_info = pd.read_table(ref_info, header=0, sep='\t')

    ref_merge = pd.merge(left=ref, right=ref_info, left_on='s', right_on='Sample', how='inner')

    data_ref = pd.concat([ref_merge, data], sort=False)
    print('\nDone merging data with ref')

    return data_ref


def assign_population_pcs(
        pop_pc_pd: pd.DataFrame,
        num_pcs: int,
        known_col: str = 'SuperPop',
        fit: RandomForestClassifier = None,
        seed: int = 42,
        prop_train: float = 0.8,
        n_estimators: int = 100,
        min_prob: float = 0.9,
        output_col: str = 'pop',
        missing_label: str = 'oth'
) -> Tuple[pd.DataFrame, RandomForestClassifier]:
    """
    This function uses a random forest model to assign population labels based on the results of PCA.
    Default values for model and assignment parameters are those used in gnomAD.
    :param Table pop_pc_pd: Pandas dataframe containing population PCs as well as a column with population labels
    :param str known_col: Column storing the known population labels
    :param RandomForestClassifier fit: fit from a previously trained random forest model (i.e., the output from a previous RandomForestClassifier() call)
    :param int num_pcs: number of population PCs on which to train the model
    :param int seed: Random seed
    :param float prop_train: Proportion of known data used for training
    :param int n_estimators: Number of trees to use in the RF model
    :param float min_prob: Minimum probability of belonging to a given population for the population to be set (otherwise set to `None`)
    :param str output_col: Output column storing the assigned population
    :param str missing_label: Label for samples for which the assignment probability is smaller than `min_prob`
    :return: Dataframe containing sample IDs and imputed population labels, trained random forest model
    :rtype: DataFrame, RandomForestClassifier
    """

    # Expand PC column
    pc_cols = ['PC{}'.format(i + 1) for i in range(num_pcs)]
    train_data = pop_pc_pd.loc[~pop_pc_pd[known_col].isnull()]

    N = len(train_data)

    # Split training data into subsamples for fitting and evaluating
    if not fit:
        random.seed(seed)
        train_subsample_ridx = random.sample(list(range(0, N)), int(N * prop_train))
        train_fit = train_data.iloc[train_subsample_ridx]
        fit_samples = [x for x in train_fit['s']]
        evaluate_fit = train_data.loc[~train_data['s'].isin(fit_samples)]

        # Train RF
        training_set_known_labels = train_fit[known_col].values
        training_set_pcs = train_fit[pc_cols].values
        evaluation_set_pcs = evaluate_fit[pc_cols].values

        pop_clf = RandomForestClassifier(n_estimators=n_estimators, random_state=seed)
        pop_clf.fit(training_set_pcs, training_set_known_labels)
        print('Random forest feature importances are as follows: {}'.format(pop_clf.feature_importances_))

        # Evaluate RF
        predictions = pop_clf.predict(evaluation_set_pcs)
        error_rate = 1 - sum(evaluate_fit[known_col] == predictions) / float(len(predictions))
        print('Estimated error rate for RF model is {}'.format(error_rate))
    else:
        pop_clf = fit

    # Classify data
    print('Classifying data')
    pop_pc_pd[output_col] = pop_clf.predict(pop_pc_pd[pc_cols].values)
    probs = pop_clf.predict_proba(pop_pc_pd[pc_cols].values)
    probs = pd.DataFrame(probs, columns=[f'prob_{p}' for p in pop_clf.classes_])

    pop_pc_pd = pd.concat([pop_pc_pd.reset_index(drop=True), probs.reset_index(drop=True)], axis=1)

    probs['max'] = probs.max(axis=1)
    pop_pc_pd.loc[probs['max'] < min_prob, output_col] = missing_label

    return pop_pc_pd, pop_clf


def plot_pca_ref(data_scores, ref_scores, ref_info, x_pc, y_pc):
    pcs = pd.read_table(data_scores, header=0, sep='\t')
    ref = pd.read_table(ref_scores, header=0, sep='\t', compression='gzip')
    ref_info = pd.read_table(ref_info, header=0, sep='\t')

    ref_info.rename(columns={'Sample': 's'}, inplace=True)
    ref_update = pd.merge(ref, ref_info, how='left', on=['s'])
    # only take s, PC1-20, and POP columns
    ref_update = ref_update.iloc[:, 0:22]

    cbPalette = {'AFR': "#984EA3", 'EAS': "#4DAF4A", 'EUR': "#377EB8", 'CSA': "#FF7F00", 'AMR': "#E41A1C",
                 'MID': "#A65628", 'OCE': "#999999", 'oth': "#F0E442"}

    # PLOT
    fig, axs = plt.subplots(nrows=1, ncols=1, figsize=(15, 15))

    # get population counts so we can add them to legend
    handles = []
    pop_counts = (pcs['pop'].value_counts(sort=True)).to_dict()

    for key in cbPalette:
        # if the key is not in the dict, add it
        if key not in pop_counts:
            pop_counts[key] = 0
        # manually define a new patch
        data_key = Line2D([0], [0], marker='o', color='w', label='{} (n={})'.format(key, pop_counts.get(key)),
                          markerfacecolor=cbPalette[key], markersize=10)
        handles.append(data_key)

    axs.scatter(ref_update[x_pc], ref_update[y_pc], c=ref_update['SuperPop'].map(cbPalette), s=5, alpha=0.1)

    axs.scatter(pcs[x_pc], pcs[y_pc], c=pcs['pop'].map(cbPalette), s=5, alpha=1)
    axs.set_xlabel(xlabel=x_pc, fontsize=15)
    axs.set_ylabel(ylabel=y_pc, fontsize=15)
    fig.legend(handles=handles, title='Populations', loc='right', frameon=False)
    plt.close()

    return fig


def pca_with_ref(
        ref_dirname: str = 'gs://african-seq-data/hgdp_tgp/gwaspy_pca_ref/',
        ref_basename: str = 'hgdp_1kg_filtered_maf_5_GRCh38',
        ref_info: str = 'gs://african-seq-data/hgdp_tgp/gwaspy_pca_ref/hgdp_1kg_sample_info.tsv',
        data_dirname: str = None,
        data_basename: str = None,
        out_dir: str = None,
        input_type: str = None,
        reference: str = 'GRCh38',
        maf: float = 0.05,
        hwe: float = 1e-3,
        call_rate: float = 0.98,
        ld_cor: float = 0.2,
        ld_window: int = 250000,
        prob_threshold: float = 0.8):
    """
    Project samples into predefined PCA space
    :param ref_dirname: directory name where reference data is
    :param ref_basename: base filename for reference data
    :param ref_info: reference sample information
    :param data_dirname: matrix table of data to project
    :param data_basename: matrix table of data to project
    :param out_dir: directory and filename prefix for where to put PCA projection output
    :param input_type: input file(s) type: hail, plink, or vcf
    :param reference: reference build
    :param maf: minor allele frequency threshold
    :param hwe: hardy-weinberg fiter threshold
    :param call_rate: variant call rate filter threshold
    :param ld_cor: reference build
    :param ld_window: window size
    :param prob_threshold: a list of probability thresholds to use for classifying samples
    :return: a pandas Dataframe with data PCA scores projected on the same PCA space using the Human Genome Diversity
    Project(HGDP) and the 1000 Genomes Project samples as reference
    """
    print('Reading data mt')
    if reference.lower() == 'grch37':
        from gwaspy.utils.reference_liftover import liftover_to_grch38
        mt = liftover_to_grch38(dirname=data_dirname, basename=data_basename, input_type=input_type)
    else:
        from gwaspy.utils.read_file import read_infile
        mt = read_infile(input_type=input_type, dirname=data_dirname, basename=data_basename)

    print("\nFiltering data mt")
    mt = pca_filter_mt(in_mt=mt, maf=maf, hwe=hwe, call_rate=call_rate, ld_cor=ld_cor, ld_window=ld_window)

    # Intersect data with reference
    intersect_ref(ref_dirname=ref_dirname, ref_basename=ref_basename, data_mt=mt, data_basename=data_basename,
                  out_dir=out_dir)

    ref_in_data = hl.read_matrix_table(out_dir + 'GWASpy/PCA/' + '1000G_intersect_' + data_basename + '.mt')

    print('\nComputing reference PCs')
    run_ref_pca(mt=ref_in_data, out_dir=out_dir)

    # project data
    pca_loadings = hl.read_table(out_dir + 'GWASpy/PCA/' + '1000G_loadings.ht')
    project_mt = hl.read_matrix_table(out_dir + 'GWASpy/PCA/' + data_basename + '_intersect_1000G.mt')

    ht_projections = pc_project(mt=project_mt, loadings_ht=pca_loadings)
    ht_projections = ht_projections.transmute(**{f'PC{i}': ht_projections.scores[i - 1] for i in range(1, 21)})
    ht_projections.export(out_dir + 'GWASpy/PCA/' + data_basename + '_scores.tsv')

    ref_scores = out_dir + 'GWASpy/PCA/' + '1000G_scores.txt.bgz'
    data_scores = out_dir + 'GWASpy/PCA/' + data_basename + '_scores.tsv'
    data_ref = merge_data_with_ref(ref_scores=ref_scores, ref_info=ref_info, data_scores=data_scores)

    pcs_df, clf = assign_population_pcs(pop_pc_pd=data_ref, num_pcs=20, min_prob=prob_threshold)

    data_pops = pcs_df.loc[pcs_df['SuperPop'].isnull()]
    data_pops['pop'].value_counts()
    cols = ['s', 'pop'] + [f'prob_{i}' for i in ["AFR", "AMR", "CSA", "EAS", "EUR", "MID", "OCE"]] + [f'PC{i}' for i in
                                                                                                      range(1, 21)]
    data_pops_df = data_pops[cols]

    data_pops_df.to_csv('{}GWASpy/PCA/pca_sup_pops_{}_probs.txt'.format(out_dir, prob_threshold),
                        sep='\t', index=False)

    print("\nGenerating PCA plots")
    data_scores_prob = out_dir + 'GWASpy/PCA/pca_sup_pops_' + str(prob_threshold) + '_probs.txt'

    figs_dict = {}
    for i in range(1, 20, 2):
        xpc = f'PC{i}'
        ypc = f'PC{i + 1}'

        figs_dict["fig{}{}".format(xpc, ypc)] = plot_pca_ref(data_scores=data_scores_prob,
                                                             ref_scores=ref_scores,
                                                             ref_info=ref_info,
                                                             x_pc=xpc, y_pc=ypc)
    pdf = PdfPages('/tmp/pca.with.ref.plots.pdf')
    for figname, figure in figs_dict.items():
        pdf.savefig(figure)
    pdf.close()
    hl.hadoop_copy(f'file:///tmp/pca.with.ref.plots.pdf',
                   '{}GWASpy/PCA/{}.pca.with.ref.plots.pdf'.format(out_dir, data_basename))



