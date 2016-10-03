"""
Computational Cancer Analysis Library

Authors:
    Huwate (Kwat) Yeerna (Medetgul-Ernar)
        kwat.medetgul.ernar@gmail.com
        Computational Cancer Analysis Laboratory, UCSD Cancer Center

    Pablo Tamayo
        ptamayo@ucsd.edu
        Computational Cancer Analysis Laboratory, UCSD Cancer Center
"""

from math import ceil, sqrt

from numpy import array, sum, unique, isnan
from numpy.random import shuffle, choice
from pandas import Series, DataFrame, read_csv, concat
from scipy.stats import norm
from statsmodels.sandbox.stats.multicomp import multipletests
from matplotlib.pyplot import figure, subplot
from matplotlib.gridspec import GridSpec
from seaborn import heatmap

from .support import print_log, establish_filepath, read_gct, title_string, untitle_string, information_coefficient, \
    parallelize, get_unique_in_order, normalize_pandas_object, compare_matrices, FIGURE_SIZE, CMAP_CONTINUOUS, \
    CMAP_CATEGORICAL, CMAP_BINARY, FONT, FONT_TITLE, save_plot, plot_clustermap


# ======================================================================================================================
# Association panel
# ======================================================================================================================
def make_association_panels(target, features_bundle, target_name=None, target_type='continuous',
                            n_jobs=1, n_features=0.95, n_samplings=30, n_permutations=30, filepath_prefix=None):
    """
    Annotate target with each feature in the features bundle.
    :param target: pandas Series; (n_elements); must have indices
    :param features_bundle: list of lists;
        [
            [name
            dataframe_or_filepath (to .gct),
            data_type,
            is_ascending,
            (optional) index_axis,
            (optional) index,
            (optional) index_alias],
            ...
        ]
    :param target_name: str;
    :param target_type:
    :param n_jobs: int; number of jobs to parallelize
    :param n_features: int or float; number threshold if >= 1, and percentile threshold if < 1
    :param n_samplings: int; number of bootstrap samplings to build distribution to get CI; must be > 2 to compute CI
    :param n_permutations: int; number of permutations for permutation test to compute P-val and FDR
    :param filepath_prefix: str; filepath_prefix_annotation_name.txt and filepath_prefix_annotation_name.pdf are saved
    :return: None
    """

    # Load feature bundle
    print_log('Loading features bundle ...')
    feature_dicts = _read_bundle(features_bundle)

    if target_name:  # Set target name
        target.name = target_name

    # Annotate this target with each feature
    for features_name, features_dict in feature_dicts.items():
        title = '{} vs {}'.format(target.name, features_name)
        print_log('{} ...'.format(title_string(title)))
        make_association_panel(target, features_dict['dataframe'],
                               target_type=target_type, features_type=features_dict['data_type'],
                               n_jobs=n_jobs, features_ascending=features_dict['is_ascending'], n_features=n_features,
                               n_samplings=n_samplings, n_permutations=n_permutations,
                               title=title_string(title), filepath_prefix=filepath_prefix + untitle_string(title))


def make_association_panel(target, features, target_name=None, target_type='continuous', features_type='continuous',
                           n_jobs=1, features_ascending=False, n_features=0.95, n_samplings=30, n_permutations=30,
                           title=None, plot_colname=False, filepath_prefix=None):
    """
    Compute: score_i = function(target, feature_i). Compute confidence interval (CI) for n_features features.
    Compute p-val and FDR (BH) for all features. And plot the result.
    :param target: pandas Series; (n_samples); must have name and index matching features's column names
    :param features: pandas DataFrame; (n_features, n_samples); must have index and column names
    :param target_name: str;
    :param target_type: str; {'continuous', 'categorical', 'binary'}
    :param features_type: str; {'continuous', 'categorical', 'binary'}
    :param n_jobs: int; number of jobs to parallelize
    :param features_ascending: bool; True if features scores increase from top to bottom, and False otherwise
    :param n_features: int or float; number threshold if >= 1, and percentile threshold if < 1
    :param n_samplings: int; number of bootstrap samplings to build distribution to get CI; must be > 2 to compute CI
    :param n_permutations: int; number of permutations for permutation test to compute P-val and FDR
    :param title: str; plot title
    :param plot_colname: bool; plot column names below the plot or not
    :param filepath_prefix: str; filepath_prefix.txt and filepath_prefix.pdf will be saved
    :return: pandas DataFrame; merged features and scores
    """

    #
    # Preprocess data
    #
    if isinstance(features, Series):  # Convert Series-features into DataFrame-features with 1 row
        features = DataFrame(features).T

    if target_name:  # Set target name
        target.name = target_name

    # Keep only columns shared by target and features
    shared = target.index & features.columns
    if any(shared):
        # Target is always descending from left to right
        target = target.ix[shared].sort_values(ascending=False)
        features = features.ix[:, target.index]
        print_log('Target {} ({} cols) and features ({} cols) have {} shared columns.'.format(target.name,
                                                                                              target.size,
                                                                                              features.shape[1],
                                                                                              len(shared)))
    else:
        raise ValueError('Target {} ({} cols) and features ({} cols) have 0 shared columns.'.format(target.name,
                                                                                                    target.size,
                                                                                                    features.shape[1]))

    # Drop features having less than 2 unique values
    min_n_unique_values = 2
    print_log('Dropping features with less than {} unique values ...'.format(min_n_unique_values))
    features = features.ix[features.apply(lambda f: len(set(f)), axis=1) >= min_n_unique_values]
    if features.empty:
        raise ValueError('No feature has at least {} unique values.'.format(min_n_unique_values))
    else:
        print_log('\tKept {} features.'.format(features.shape[0]))

    #
    # Score
    #
    if filepath_prefix:
        filepath = filepath_prefix + '.txt'
    else:
        filepath = None
    scores = associate(target, features, n_jobs=n_jobs, features_ascending=features_ascending,
                       n_features=n_features, n_samplings=n_samplings, n_permutations=n_permutations, filepath=filepath)

    # Concatenate
    features = concat([features, scores], join_axes=[scores.index], axis=1)

    #
    # Make annotations
    #
    annotations = DataFrame(index=features.index, columns=['IC(\u0394)', 'P-val', 'FDR'])

    # Add IC (0.95 confidence interval)
    for f_i, s_moe in features.ix[:, ['score', '0.95 moe']].iterrows():
        if isnan(s_moe.ix['0.95 moe']):
            a = '{0:.3f}(x.xxx)'.format(s_moe.ix['score'])
        else:
            a = '{0:.3f}({1:.3f})'.format(*s_moe.ix[['score', '0.95 moe']])
        annotations.ix[f_i, 'IC(\u0394)'] = a

    # Add P-val
    if 'p-value' in features.columns:
        a = ['{:.2e}'.format(pv) for pv in features.ix[:, 'p-value']]
    else:
        a = 'x.xxe\u00B1xx'
    annotations.ix[:, 'P-val'] = a

    # Add FDR
    if 'fdr' in features.columns:
        a = ['{:.2e}'.format(fdr) for fdr in features.ix[:, 'fdr']]
    else:
        a = 'x.xxe\u00B1xx'
    annotations.ix[:, 'FDR'] = a

    #
    # Plot
    #
    # Limited features to plot
    if n_features > 100 or (n_features < 1 and n_features * features.shape[0] > 100):
        n_features = 100
        print_log('Changed n_features to be 100 because using {} results in plotting too many features.')

    if n_features < 1:  # Limit using percentile
        # Limit top features
        above_quantile = features.ix[:, 'score'] >= features.ix[:, 'score'].quantile(n_features)
        print_log('Plotting {} features (> {:.02f} percentile) ...'.format(sum(above_quantile), n_features))

        # Limit bottom features
        below_quantile = features.ix[:, 'score'] <= features.ix[:, 'score'].quantile(1 - n_features)
        print_log('Plotting {} features (< {:.02f} percentile) ...'.format(sum(below_quantile), 1 - n_features))

        indices_to_plot = features.index[above_quantile | below_quantile].tolist()

    else:  # Limit using numbers assuming that features is sorted
        if 2 * n_features >= features.shape[0]:
            indices_to_plot = features.index
            print_log('Plotting all {} features ...'.format(features.shape[0]))
        else:
            indices_to_plot = features.index[:n_features].tolist() + features.index[-n_features:].tolist()
            print_log('Plotting top & bottom {} features ...'.format(n_features))

    # Plot
    if filepath_prefix:
        filepath = filepath_prefix + '.pdf'
    else:
        filepath = None
    _plot_association_panel(target, features.ix[indices_to_plot, :-len(scores.columns)],
                            annotations.ix[indices_to_plot, :], target_type=target_type, features_type=features_type,
                            title=title, plot_colname=plot_colname, filepath=filepath)

    return scores


def associate(target, features, function=information_coefficient, n_jobs=1, features_ascending=False,
              n_features=0.95, min_n_per_job=100, n_samplings=30, confidence=0.95, n_permutations=30, filepath=None):
    """
    Compute: score_i = function(target, feature_i).
    Compute confidence interval (CI) for n_features features. And compute p-val and FDR (BH) for all features.
    :param target: pandas Series; (n_samples); must have name and indices, matching features's column index
    :param features: pandas DataFrame; (n_features, n_samples); must have row and column indices
    :param function: function; scoring function
    :param n_jobs: int; number of jobs to parallelize
    :param features_ascending: bool; True if features scores increase from top to bottom, and False otherwise
    :param n_features: int or float; number of features to compute confidence interval and plot;
                        number threshold if >= 1, percentile threshold if < 1, and don't compute if None
    :param min_n_per_job: int; minimum number of n per job for parallel computing
    :param n_samplings: int; number of bootstrap samplings to build distribution to get CI; must be > 2 to compute CI
    :param confidence: float; fraction compute confidence interval
    :param n_permutations: int; number of permutations for permutation test to compute P-val and FDR
    :param filepath: str;
    :return: DataFrame; (n_features,
                         7 ('score', '<confidence> moe', 'p-value', 'fdr (forward)', 'fdr (reverse)', and 'fdr'))
    """

    #
    # Compute: score_i = function(target, feature_i).
    #
    if n_jobs == 1:  # Non-parallel computing
        print_log('Scoring ...')
        scores = _score((target, features, function))

    else:  # Parallel computing

        # Compute n per job
        n_per_job = features.shape[0] // n_jobs

        if n_per_job < min_n_per_job:  # n is not enough for parallel computing
            print_log('Scoring (with n_jobs=1 because n_per_job ({}) < min_n_per_job ({})) ...'.format(n_per_job,
                                                                                                       min_n_per_job))
            scores = _score((target, features, function))

        else:  # n is enough for parallel computing
            print_log('Scoring (n_jobs={}) ...'.format(n_jobs))

            # Group
            args = []
            leftovers = list(features.index)
            for i in range(n_jobs):
                split_features = features.iloc[i * n_per_job: (i + 1) * n_per_job, :]
                args.append((target, split_features, function))

                # Remove scored features
                for feature in split_features.index:
                    leftovers.remove(feature)

            # Parallelize
            scores = concat(parallelize(_score, args, n_jobs=n_jobs))

            # Score leftovers
            if leftovers:
                print_log('Scoring leftovers: {} ...'.format(leftovers))
                scores = concat(
                    [scores, _score((target, features.ix[leftovers, :], function))])

    # Sort by score
    scores.sort_values('score', ascending=features_ascending, inplace=True)

    #
    #  Compute CI using bootstrapped distribution
    #
    if not (isinstance(n_features, int) or isinstance(n_features, float)):
        print_log('Not computing CI because n_features = None.')

    elif n_samplings < 2:
        print_log('Not computing CI because n_samplings < 2.')

    elif ceil(0.632 * features.shape[1]) < 3:
        print_log('Not computing CI because 0.632 * n_samples < 3.')
    else:
        print_log('Computing {} CI for using distributions built by {} bootstraps ...'.format(confidence, n_samplings))
        if n_features < 1:  # Limit using percentile
            # Top features
            top_quantile = scores.ix[:, 'score'] >= scores.ix[:, 'score'].quantile(n_features)
            print_log('\tBootstrapping {} features >= {:.3f} percentile ...'.format(sum(top_quantile),
                                                                                    n_features))

            # Bottom features
            bottom_quantile = scores.ix[:, 'score'] <= scores.ix[:, 'score'].quantile(1 - n_features)
            print_log('\tBootstrapping {} features <= {:.3f} percentile ...'.format(sum(bottom_quantile),
                                                                                    1 - n_features))

            indices_to_bootstrap = scores.index[top_quantile | bottom_quantile].tolist()

        else:  # Limit using numbers, assuming that scores are sorted already
            if 2 * n_features >= scores.shape[0]:  # Number of features to compute CI for > number of total features
                indices_to_bootstrap = scores.index
                print_log('\tBootstrapping all {} features ...'.format(scores.shape[0]))

            else:
                indices_to_bootstrap = scores.index[:n_features].tolist() + scores.index[-n_features:].tolist()
                print_log('\tBootstrapping top & bottom {} features ...'.format(n_features))

        confidence_intervals = DataFrame(index=indices_to_bootstrap, columns=['{} moe'.format(confidence)])

        # Bootstrap: for n_sampling times, randomly choose 63.2% of the samples, score, and build score distribution
        sampled_scores = DataFrame(index=indices_to_bootstrap, columns=range(n_samplings))
        for c_i in sampled_scores:
            # Randomize
            ramdom_samples = choice(features.columns.tolist(), int(ceil(0.632 * features.shape[1]))).tolist()
            sampled_features = features.ix[indices_to_bootstrap, ramdom_samples]
            sampled_target = target.ix[ramdom_samples]
            # Score
            sampled_scores.ix[:, c_i] = sampled_features.apply(lambda f: function(f, sampled_target), axis=1)

        # Compute scores' confidence intervals using bootstrapped score distributions
        # TODO: improve confidence interval calculation
        z_critical = norm.ppf(q=confidence)
        confidence_intervals.ix[:, '{} moe'.format(confidence)] = sampled_scores.apply(
            lambda f: z_critical * (f.std() / sqrt(n_samplings)), axis=1)

        # Concatenate
        scores = concat([scores, confidence_intervals], join_axes=[scores.index], axis=1)

    #
    # Compute P-values and FDRs by sores against permuted target
    #
    if n_permutations < 1:
        print_log('Not computing P-value and FDR because n_perm < 1.')
    else:
        p_values_and_fdrs = DataFrame(index=scores.index,
                                      columns=['p-value', 'fdr (forward)', 'fdr (reverse)', 'fdr'])

        if n_jobs == 1:  # Non-parallel computing
            print_log('Computing P-value & FDR by scoring against {} permuted targets ...'.format(n_permutations))
            permutation_scores = _permute_and_score((target, features, function, n_permutations))

        else:  # Parallel computing

            # Compute n for a job
            n_per_job = features.shape[0] // n_jobs

            if n_per_job < min_n_per_job:  # n is not enough for parallel computing
                print_log('Computing P-value & FDR by scoring against {} permuted targets'
                          '(with n_jobs=1 because n_per_jobs ({}) < min_n_jobs ({})) ...'.format(n_permutations,
                                                                                                 n_per_job,
                                                                                                 min_n_per_job))
                permutation_scores = _permute_and_score((target,
                                                         features,
                                                         function,
                                                         n_permutations))

            else:  # n is enough for parallel computing
                print_log('Computing P-value & FDR by scoring against {} permuted targets'
                          '(n_jobs={}) ...'.format(n_permutations,
                                                   n_jobs))

                # Group
                args = []
                leftovers = list(features.index)
                for i in range(n_jobs):
                    split_features = features.iloc[i * n_per_job: (i + 1) * n_per_job, :]
                    args.append((target, split_features, function, n_permutations))

                    # Remove scored features
                    for feature in split_features.index:
                        leftovers.remove(feature)

                # Parallelize
                permutation_scores = concat(parallelize(_permute_and_score, args, n_jobs=n_jobs))

                # Handle leftovers
                if leftovers:
                    print_log('Scoring against permuted target using leftovers: {} ...'.format(leftovers))
                    permutation_scores = concat([permutation_scores,
                                                 _permute_and_score((target,
                                                                     features.ix[leftovers, :],
                                                                     function,
                                                                     n_permutations))])

        # Compute local and global P-values
        print_log('\tComputing P-value and FDR ...')
        all_permutation_scores = permutation_scores.values.flatten()
        for i, (r_i, r) in enumerate(scores.iterrows()):
            # Compute global p-value
            p_value = float(sum(all_permutation_scores > float(r.ix['score'])) / (n_permutations * features.shape[0]))
            if not p_value:
                p_value = float(1 / (n_permutations * scores.shape[0]))
            p_values_and_fdrs.ix[r_i, 'p-value'] = p_value

        # Compute global permutation FDRs
        p_values_and_fdrs.ix[:, 'fdr (forward)'] = multipletests(p_values_and_fdrs.ix[:, 'p-value'], method='fdr_bh')[1]
        p_values_and_fdrs.ix[:, 'fdr (reverse)'] = \
            multipletests(1 - p_values_and_fdrs.ix[:, 'p-value'], method='fdr_bh')[1]
        p_values_and_fdrs.ix[:, 'fdr'] = p_values_and_fdrs.ix[:, ['fdr (forward)', 'fdr (reverse)']].min(axis=1)

        # Concatenate
        scores = concat([scores, p_values_and_fdrs], join_axes=[scores.index], axis=1)

    # Save
    if filepath:
        establish_filepath(filepath)
        scores.to_csv(filepath, sep='\t')

    return scores


def _score(args):
    """
    Compute: ith score = function(target, ith feature).
    :param args: list-like;
        (DataFrame (n_features, m_samples); features, Series (m_samples); target, function)
    :return: pandas DataFrame; (n_features, 1 ('score'))
    """

    t, f, func = args
    return DataFrame(f.apply(lambda a_f: func(t, a_f), axis=1), index=f.index, columns=['score'], dtype=float)


def _permute_and_score(args):
    """
    Compute: ith score = function(target, ith feature) for n_permutations times.
    :param args: list-like;
        (Series (m_samples); target,
         DataFrame (n_features, m_samples); features,
         function,
         int; n_permutations)
    :return: pandas DataFrame; (n_features, n_permutations)
    """

    t, f, func, n_perms = args

    scores = DataFrame(index=f.index, columns=range(n_perms))
    shuffled_target = array(t)
    for p in range(n_perms):
        print_log('\tScoring against permuted target ({}/{}) ...'.format(p, n_perms))
        shuffle(shuffled_target)
        scores.iloc[:, p] = f.apply(lambda r: func(shuffled_target, r), axis=1)
    return scores


def _plot_association_panel(target, features, annotations, target_type='continuous', features_type='continuous',
                            title=None, plot_colname=False, filepath=None):
    """
    Plot association panel.
    :param target: pandas Series; (n_elements); must have indices matching features's columns
    :param features: pandas DataFrame; (n_features, n_elements); must have indices and columns
    :param annotations: pandas DataFrame; (n_features, n_annotations); must have indices matching features's index
    :param target_type: str; {'continuous', 'categorical', 'binary'}
    :param features_type: str; {'continuous', 'categorical', 'binary'}
    :param title: str;
    :param plot_colname: bool; plot column names or not
    :param filepath: str;
    :return: None
    """

    # Prepare target for plotting
    target, target_min, target_max, target_cmap = _prepare_data_for_plotting(target, target_type)

    # Prepare features for plotting
    features, features_min, features_max, features_cmap = _prepare_data_for_plotting(features, features_type)

    # Set up figure
    figure(figsize=(min(pow(features.shape[1], 0.7), 7), pow(features.shape[0], 0.9)))

    # Set up axis grids
    gridspec = GridSpec(features.shape[0] + 1, 1)
    # Set up axes
    target_ax = subplot(gridspec[:1, 0])
    features_ax = subplot(gridspec[1:, 0])

    #
    # Plot target, target label, and title
    #
    # Plot target
    heatmap(DataFrame(target).T, ax=target_ax, vmin=target_min, vmax=target_max, cmap=target_cmap,
            xticklabels=False, cbar=False)

    # Adjust target name
    for t in target_ax.get_yticklabels():
        t.set(**FONT)

    if target_type in ('binary', 'categorical'):  # Add binary or categorical target labels
        boundaries = [0]

        # Get values
        prev_v = target.iloc[0]
        for i, v in enumerate(target.iloc[1:]):
            if prev_v != v:
                boundaries.append(i + 1)
            prev_v = v
        boundaries.append(features.shape[1])

        # Get positions
        label_horizontal_positions = []
        prev_b = 0
        for b in boundaries[1:]:
            label_horizontal_positions.append(b - (b - prev_b) / 2)
            prev_b = b
        unique_target_labels = get_unique_in_order(target.values)

        # Plot values to their corresponding positions
        for i, x in enumerate(label_horizontal_positions):
            target_ax.text(x, target_ax.axis()[3] * 1.1, unique_target_labels[i], horizontalalignment='center', **FONT)

    if title:  # Plot title
        target_ax.text(target_ax.axis()[1] * 0.5, target_ax.axis()[3] * 1.7, title, horizontalalignment='center',
                       **FONT_TITLE)

    # Plot annotation header
    target_ax.text(target_ax.axis()[1] + target_ax.axis()[1] * 0.01, target_ax.axis()[3] * 0.5,
                   ' ' * 6 + 'IC(\u0394)' + ' ' * 12 + 'P-val' + ' ' * 14 + 'FDR', verticalalignment='center', **FONT)

    # Plot features
    heatmap(features, ax=features_ax, vmin=features_min, vmax=features_max, cmap=features_cmap,
            xticklabels=plot_colname, cbar=False)
    for t in features_ax.get_yticklabels():
        t.set(**FONT)

    # Plot features' annotations
    for i, (a_i, a) in enumerate(annotations.iterrows()):
        features_ax.text(features_ax.axis()[1] + features_ax.axis()[1] * 0.01, features_ax.axis()[3] - i - 0.5,
                         '\t'.join(a.tolist()).expandtabs(), verticalalignment='center', **FONT)

    # Save
    if filepath:
        save_plot(filepath)


def plot_summary_association_panel(target, features_bundle, annotations_bundle, target_type='continuous',
                                   filepath=None):
    """
    Plot summary association panel.
    :param target: pandas Series; (n_elements); must have indices
    :param features_bundle: list;
        [
            [name
            dataframe_or_filepath (to .gct),
            data_type,
            is_ascending,
            (optional) index_axis,
            (optional) index,
            (optional) index_alias],
            ...
        ]
    :param annotations_bundle:
        [
            [name
            dataframe_or_filepath (to annotation .gct)],
            ...
        ]
    :param target_type: str;
    :param filepath: str;
    :return: None
    """

    # Read features
    features_dicts = _read_bundle(features_bundle)

    # Prepare target for plotting
    target, target_min, target_max, target_cmap = _prepare_data_for_plotting(target, target_type)

    #
    # Set up figure
    #
    # Compute the number of row-grids for setting up a figure
    n = 0
    for features_name, features_dict in features_dicts.items():
        n += features_dict['dataframe'].shape[0] + 3

    # Set up figure
    figure(figsize=FIGURE_SIZE)

    # Set up axis grids
    gridspec = GridSpec(n, 1)

    #
    # Annotate this target with each feature
    #
    r_i = 0
    header = True
    for features_name, features_dict in features_dicts.items():
        # Read features
        features = features_dict['dataframe']

        # Prepare features for plotting
        features, features_min, features_max, features_cmap = _prepare_data_for_plotting(features,
                                                                                         features_dict['data_type'])

        # Keep only columns shared by target and features
        shared = target.index & features.columns
        if any(shared):
            # Target is always descending from right to left
            a_target = target.ix[shared].sort_values(ascending=False)
            features = features.ix[:, a_target.index]
            print_log('Target {} ({} cols) and features ({} cols) have {} shared columns.'.format(target.name,
                                                                                                  target.size,
                                                                                                  features.shape[1],
                                                                                                  len(shared)))
        else:
            raise ValueError('Target {} ({} cols) and features ({} cols) have 0 shared columns.'.format(target.name,
                                                                                                        target.size,
                                                                                                        features.shape[
                                                                                                            1]))

        # Read corresponding annotations
        annotations = read_csv([a[1] for a in annotations_bundle if a[0] == features_name][0], sep='\t', index_col=0)
        # TODO: change to score
        annotations = annotations.ix[features.index, :].sort_values('Score', ascending=features_dict['is_ascending'])
        features = features.ix[annotations.index, :]

        # Set up axes
        r_i += 1
        title_ax = subplot(gridspec[r_i: r_i + 1, 0])
        title_ax.axis('off')
        r_i += 1
        target_ax = subplot(gridspec[r_i: r_i + 1, 0])
        r_i += 1
        features_ax = subplot(gridspec[r_i: r_i + features.shape[0], 0])
        r_i += features.shape[0]

        # Plot title
        title_ax.text(title_ax.axis()[1] * 0.5, title_ax.axis()[3] * 0.3,
                      '{} (n={})'.format(features_name, len(shared)), horizontalalignment='center', **FONT_TITLE)

        # Plot target
        heatmap(DataFrame(a_target).T, ax=target_ax, vmin=target_min, vmax=target_max, cmap=target_cmap,
                xticklabels=False, yticklabels=False, cbar=False)

        if header:  # Plot header only for the 1st target axis
            target_ax.text(target_ax.axis()[1] + target_ax.axis()[1] * 0.01, target_ax.axis()[3] * 0.5,
                           ' ' * 1 + 'IC(\u0394)' + ' ' * 6 + 'P-val' + ' ' * 15 + 'FDR', verticalalignment='center',
                           **FONT)
            header = False

        # Plot features
        heatmap(features, ax=features_ax, vmin=features_min, vmax=features_max, cmap=features_cmap, xticklabels=False,
                cbar=False)
        for t in features_ax.get_yticklabels():
            t.set(**FONT)

        # Plot annotations for each feature
        for i, (a_i, a) in enumerate(annotations.iterrows()):
            # TODO: lower case
            features_ax.text(features_ax.axis()[1] + features_ax.axis()[1] * 0.01,
                             features_ax.axis()[3] - i - (features_ax.axis()[1] / features.shape[0]) * 0.5,
                             '{0:.3f}\t{1:.2e}\t{2:.2e}'.format(*a.ix[['Score', 'P-value', 'FDR']]).expandtabs(),
                             verticalalignment='center', **FONT)

    # Save
    if filepath:
        save_plot(filepath)


def _prepare_data_for_plotting(dataframe, data_type, max_std=3):
    if data_type == 'continuous':
        return normalize_pandas_object(dataframe, method='-0-', axis=1), -max_std, max_std, CMAP_CONTINUOUS
    elif data_type == 'categorical':
        return dataframe.copy(), 0, len(unique(dataframe)), CMAP_CATEGORICAL
    elif data_type == 'binary':
        return dataframe.copy(), 0, 1, CMAP_BINARY
    else:
        raise ValueError('Target data type must be one of {continuous, categorical, binary}.')


def _read_bundle(data_bundle):
    """
    Read data bundle.
    :param data_bundle: list;
        [
            [name
            dataframe_or_filepath (to .gct),
            data_type,
            is_ascending,
            (optional) index_axis,
            (optional) index,
            (optional) index_alias],
            ...
        ]
    :return: dict; {name: {dataframe: DataFrame,
                    data_type: str,
                    is_ascending: bool}}
    """

    dicts = {}

    # Read all annotations
    for name, dataframe_or_filepath, data_type, is_ascending, index_axis, index, index_alias in data_bundle:
        print_log('Reading {} ...'.format(name))
        print_log('\tData: {}.'.format(type(dataframe_or_filepath)))
        print_log('\tData type: {}.'.format(data_type))
        print_log('\tIs ascending: {}.'.format(is_ascending))
        print_log('\tIndex axis: {}.'.format(index_axis))
        print_log('\tIndex: {}.'.format(index))
        print_log('\tIndex alias: {}.'.format(index_alias))

        dicts[name] = {}

        # Read data type
        dicts[name]['data_type'] = data_type

        # Read whether reverse make_association_panel or not
        dicts[name]['is_ascending'] = is_ascending

        # Read DataFrame
        if isinstance(dataframe_or_filepath, DataFrame):
            df = dataframe_or_filepath
        elif isinstance(dataframe_or_filepath, str):
            df = read_gct(dataframe_or_filepath)
        else:
            raise ValueError('dataframe_or_filepath (2nd in the list) must be either a DataFrame or str (filepath).')

        # Limit to specified features
        if index:  # Extract

            if index_axis == 0:  # By row
                df = df.ix[index, :]
                if isinstance(df, Series):
                    df = DataFrame(df).T

            elif index_axis == 1:  # By column
                df = df.ix[:, index]
                if isinstance(df, Series):
                    df = DataFrame(df).T
                else:
                    df = df.T
            else:
                raise ValueError('index_axis (6th in the list) must be either 0 (row) or 1 (column).')

            if index_alias:  # Use aliases instead of index
                if isinstance(index_alias, str):  # Wrap string with list
                    index_alias = [index_alias]
                df.index = index_alias

        dicts[name]['dataframe'] = df

        print_log('\tRead {} features & {} samples.'.format(*dicts[name]['dataframe'].shape))

    return dicts


# ======================================================================================================================
# Comparison panel
# ======================================================================================================================
def make_comparison_matrix(matrix1, matrix2, function=information_coefficient, axis=0, is_distance=False, title=None,
                           filepath_prefix=None):
    """
    Compare matrix1 and matrix2 by row (axis=1) or by column (axis=0), and plot cluster map.
    :param matrix1: pandas DataFrame or numpy 2D arrays;
    :param matrix2: pandas DataFrame or numpy 2D arrays;
    :param function: function; association or distance function
    :param axis: int; 0 for row-wise and 1 for column-wise comparison
    :param is_distance: bool; if True, distances are computed from associations, as in 'distance = 1 - association'
    :param title: str; plot title
    :param filepath_prefix: str; filepath_prefix.txt and filepath_prefix.pdf will be saved
    :return: pandas DataFrame; association or distance matrix
    """

    # Compute association or distance matrix, which is returned at the end
    comparison_matrix = compare_matrices(matrix1, matrix2, function, axis=axis, is_distance=is_distance)

    # Save
    if filepath_prefix:
        comparison_matrix.to_csv(filepath_prefix + '.txt', sep='\t')

    # Plot cluster map of the compared matrix
    if filepath_prefix:
        filepath = filepath_prefix + '.pdf'
    else:
        filepath = None
    plot_clustermap(comparison_matrix, title=title, filepath=filepath)

    return comparison_matrix
