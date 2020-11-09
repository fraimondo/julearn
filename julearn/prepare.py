# Authors: Federico Raimondo <f.raimondo@fz-juelich.de>
#          Sami Hamdan <s.hamdan@fz-juelich.de>
# License: AGPL
from julearn.transformers.target import TargetTransfromerWrapper
import pandas as pd
import numpy as np
from copy import deepcopy
from sklearn import model_selection
from sklearn.model_selection import RepeatedKFold, GridSearchCV
from sklearn.base import clone
from sklearn.model_selection import check_cv

from . estimators import get_model
from . transformers import get_transformer
from . scoring import get_extended_scorer
from . model_selection import wrap_search
from . utils import raise_error, warn, logger


def _validate_input_data(X, y, confounds, df, groups):
    if df is None:
        # Case 1: we don't have a dataframe in df

        # X must be np.ndarray with at most 2d
        if not isinstance(X, np.ndarray):
            raise_error(
                'X must be a numpy array if no dataframe is specified')

        if X.ndim not in [1, 2]:
            raise_error('X must be at most bi-dimensional')

        # Y must be np.ndarray with 1 dimension
        if not isinstance(y, np.ndarray):
            raise_error(
                'y must be a numpy array if no dataframe is specified')

        if y.ndim != 1:
            raise_error('y must be one-dimensional')

        # Same number of elements
        if X.shape[0] != y.shape[0]:
            raise_error(
                'The number of samples in X do not match y '
                '(X.shape[0] != y.shape[0]')

        if confounds is not None:
            if not isinstance(confounds, np.ndarray):
                raise_error(
                    'confounds must be a numpy array if no dataframe is '
                    'specified')

            if confounds.ndim not in [1, 2]:
                raise_error('confounds must be at most bi-dimensional')

            if X.shape[0] != confounds.shape[0]:
                raise_error(
                    'The number of samples in X do not match confounds '
                    '(X.shape[0] != confounds.shape[0]')

        if groups is not None:
            if not isinstance(groups, np.ndarray):
                raise_error(
                    'groups must be a numpy array if no dataframe is '
                    'specified')

            if groups.ndim != 1:
                raise_error('groups must be one-dimensional')

    else:
        # Case 2: we have a dataframe. X, y and confounds must be columns
        # in the dataframe
        if not isinstance(X, (str, list)):
            raise_error('X must be a string or list of strings')

        if not isinstance(y, str):
            raise_error('y must be a string')

        # Confounds can be a string, list or none
        if not isinstance(confounds, (str, list, type(None))):
            raise_error('If not None, confounds must be a string or list '
                        'of strings')

        if not isinstance(groups, (str, type(None))):
            raise_error('groups must be a string')

        if not isinstance(df, pd.DataFrame):
            raise_error('df must be a pandas.DataFrame')

        if not isinstance(X, list):
            X = [X]
        missing_columns = [t_x for t_x in X if t_x not in df.columns]
        if len(missing_columns) > 0:
            raise_error(
                'All elements of X must be in the dataframe. '
                f'The following are missing: {missing_columns}')

        if y not in df.columns:
            raise_error(
                f"Target '{y}' (y) is not a valid column in the dataframe")

        if confounds is not None:
            if not isinstance(confounds, list):
                confounds = [confounds]
            missing_columns = [
                t_c for t_c in confounds if t_c not in df.columns]
            if len(missing_columns) > 0:
                raise_error(
                    'All elements of confounds must be in the dataframe. '
                    f'The following are missing: {missing_columns}')

        if groups is not None:
            if groups not in df.columns:
                raise_error(f"Groups '{groups}' is not a valid column "
                            "in the dataframe")
            if groups == y:
                warn("y and groups are the same column")
            if groups in X:
                warn("groups is part of X")

        if y in X:
            warn("y is part of X")


def prepare_input_data(X, y, confounds, df, pos_labels, groups):
    """ Prepare the input data and variables for the pipeline

    Parameters
    ----------
    X : str, list(str) or numpy.array
        The features to use.
        See https://juaml.github.io/julearn/input.html for details.
    y : str or numpy.array
        The targets to predict.
        See https://juaml.github.io/julearn/input.html for details.
    confounds : str, list(str) or numpy.array | None
        The confounds.
        See https://juaml.github.io/julearn/input.html for details.
    df : pandas.DataFrame with the data. | None
        See https://juaml.github.io/julearn/input.html for details.
    pos_labels : str, int, float or list | None
        The labels to interpret as positive. If not None, every element from y
        will be converted to 1 if is equal or in pos_labels and to 0 if not.
    groups : str or numpy.array | None
        The grouping labels in case a Group CV is used.
        See https://juaml.github.io/julearn/input.html for details.

    Returns
    -------
    df_X_conf : pandas.DataFrame
        A dataframe with the features and confounds (if specified in the
        confounds parameter) for each sample.
    df_y : pandas.Series
        A series with the y variable (target) for each sample.
    df_groups : pandas.Series
        A series with the grouping variable for each sample (if specified
        in the groups parameter).
    confound_names : str
        The name of the columns if df_X_conf that represent confounds.

    """
    logger.info('==== Input Data ====')
    _validate_input_data(X, y, confounds, df, groups)

    # Declare them as None to avoid CI issues
    df_X_conf = None
    confound_names = None
    df_groups = None
    if df is None:
        logger.info(f'Using numpy arrays as input')
        # creating df_X_conf
        if X.ndim == 1:
            X = X[:, None]
        logger.info(f'# Samples: {X.shape[0]}')
        logger.info(f'# Features: {X.shape[1]}')
        columns = [f'feature_{i}' for i in range(X.shape[1])]
        df_X_conf = pd.DataFrame(X, columns=columns)

        # adding confounds to df_X_conf
        if confounds is not None:
            if confounds.ndim == 1:
                confounds = confounds[:, None]
            logger.info(f'# Confounds: {X.shape[1]}')
            confound_names = [
                f'confound_{i}' for i in range(confounds.shape[1])]
            df_X_conf[confound_names] = confounds

        # creating a Series for y if not existent
        df_y = pd.Series(y, name='y')

        if groups is not None:
            logger.info('Using groups')
            df_groups = pd.Series(groups, name='groups')

    else:
        logger.info(f'Using dataframe as input')
        logger.info(f'Features: {X}')
        logger.info(f'Target: {y}')
        X_conf_columns = deepcopy(X) if isinstance(X, list) else [X]
        if confounds is not None:
            if not isinstance(confounds, list):
                confounds = [confounds]
            logger.info(f'Confounds: {confounds}')
            overlapping = [t_c for t_c in confounds if t_c in X]
            if len(overlapping) > 0:
                warn(f'X contains the following confounds {overlapping}')
            for t_c in confounds:
                # This will add the confounds if not there already
                if t_c not in X_conf_columns:
                    X_conf_columns.append(t_c)

        df_X_conf = df.loc[:, X_conf_columns].copy()
        df_y = df.loc[:, y].copy()
        if groups is not None:
            logger.info(f'Using {groups} as groups')
            df_groups = df.loc[:, groups].copy()
        confound_names = confounds

    if pos_labels is not None:
        if not isinstance(pos_labels, list):
            pos_labels = [pos_labels]
        logger.info(f'Setting the following as positive labels {pos_labels}')
        # TODO: Warn if pos_labels are not in df_y
        df_y = df_y.isin(pos_labels).astype(np.int)
    logger.info('====================')
    logger.info('')
    return df_X_conf, df_y, df_groups, confound_names


def prepare_model(model, problem_type):
    """ Get the propel model/name pair from the input

    Parameters
    ----------
    model: str or sklearn.base.BaseEstimator
        str/model_name that can be read in to create a model.
    problem_type: str
        binary_classification, multiclass_classification or regression

    Returns
    -------
    model_name : str
        The model name
    model : object
        The model

    """
    logger.info('====== Model ======')
    if isinstance(model, str):
        logger.info(f'Obtaining model by name: {model}')
        model_name = model
        model = get_model(model_name, problem_type)
    elif _is_valid_sklearn_model(model):
        model_name = model.__class__.__name__.lower()
        logger.info(f'Using scikit-learn model: {model_name}')
    else:
        raise_error(
            f'Model must be a string or a scikit-learn compatible object.')
    logger.info('===================')
    logger.info('')
    return model_name, model


def prepare_model_selection(msel_dict, pipeline, model_name, cv_outer):
    logger.info('= Model Parameters =')
    hyperparameters = msel_dict.get('hyperparameters', None)
    if hyperparameters is None:
        raise_error("The 'hyperparameters' value must be specified for "
                    "model selection.")

    hyper_params = _prepare_hyperparams(hyperparameters, pipeline, model_name)

    if len(hyper_params) > 0:
        logger.info('Tunning hyperparameters using Grid Search')
        logger.info('Hyperparameters:')
        for k, v in hyper_params.items():
            logger.info(f'\t{k}: {v}')
        cv_inner = msel_dict.get('cv', None)
        gs_scoring = msel_dict.get('scoring', None)
        if cv_inner is None:
            logger.info(
                'Cross validating using same scheme as for model evaluation')
            cv_inner = deepcopy(cv_outer)
        else:
            logger.info(f'Cross validating using {cv_inner}')
            cv_inner = prepare_cv(cv_inner)

        if gs_scoring is not None:
            logger.info(f'Grid Search scoring: {gs_scoring}')
        pipeline = wrap_search(
            GridSearchCV, pipeline, hyper_params, cv=cv_inner,
            scoring=gs_scoring)
    logger.info('====================')
    logger.info('')
    return pipeline


def _prepare_hyperparams(hyperparams, pipeline, model_name):

    def rename_param(param):
        first, *rest = param.split('__')

        if first == 'features':
            new_first = 'dataframe_pipeline'
        elif first == 'confounds':
            new_first = 'confound_dataframe_pipeline'
        elif first == 'target':
            new_first = 'y_transformer'
        elif first == model_name:
            new_first = 'dataframe_pipeline__' + first

        else:
            raise_error(
                'Each element of the hyperparameters dict  has to start with '
                f'"features__", "confounds__", "target__" or "{model_name}__" '
                f'but was {first}')
        return '__'.join([new_first] + rest)

    to_tune = {}
    for param, val in hyperparams.items():
        # If we have more than 1 value, we will tune it. If not, it will
        # be set in the model.
        if hasattr(val, '__iter__') and not isinstance(val, str):
            if len(val) > 1:
                to_tune[rename_param(param)] = val
            else:
                logger.info(f'Setting hyperparameter {val}')
                pipeline.set_param(val)
        else:
            pipeline.set_params(**{rename_param(param): val})
    return to_tune


def prepare_preprocessing(preprocess_X, preprocess_y, preprocess_confounds):
    if not isinstance(preprocess_X, list):
        preprocess_X = [preprocess_X]
    if not isinstance(preprocess_confounds, list):
        preprocess_confounds = [preprocess_confounds]
    preprocess_X = _prepare_preprocess_X(preprocess_X)
    preprocess_y = _prepare_preprocess_y(preprocess_y)
    preprocess_conf = _prepare_preprocess_confounds(preprocess_confounds)
    return preprocess_X, preprocess_y, preprocess_conf


def _prepare_preprocess_X(preprocess_X):
    '''
    validates preprocess_X and returns a list of tuples accordingly
    and default params for this list
    '''

    preprocess_X = [_create_preprocess_tuple(transformer)
                    for transformer in preprocess_X]
    return preprocess_X


def _get_confound_transformer(conf):
    returned_features = 'unknown_same_type'
    if isinstance(conf, str):
        conf, returned_features = get_transformer(conf)
    elif not _is_valid_sklearn_transformer(conf):
        raise_error(
            f'The specified confound preprocessing ({conf}) is not valid.'
            f'It has to be a string or sklearn transformer.')
    return conf, returned_features


def _prepare_preprocess_confounds(preprocess_conf):
    '''
    uses user input to create a list of tuples for a normal pipeline
    this can then be used for transforming the confounds/z
    '''
    if not isinstance(preprocess_conf, list):
        preprocess_conf = [preprocess_conf]

    returned_features = 'unknown_same_type'
    for step in preprocess_conf:
        _, returned_features = _get_confound_transformer(step)

        if returned_features == 'unknown':
            returned_features = 'unknown_same_type'

    preprocess_conf = [
        # returned_features ignored
        _create_preprocess_tuple(transformer)
        for transformer in preprocess_conf

    ]
    # replace returned_feature with what we got here
    preprocess_conf = [step[:2] + (returned_features,) + (step[-1],)
                       for step in preprocess_conf]

    return preprocess_conf


def _prepare_preprocess_y(preprocess_y):
    if preprocess_y is not None:
        if isinstance(preprocess_y, str):
            preprocess_y = get_transformer(preprocess_y, target=True)
        elif not isinstance(preprocess_y, TargetTransfromerWrapper):
            if _is_valid_sklearn_transformer(preprocess_y):
                preprocess_y = TargetTransfromerWrapper(preprocess_y)
            else:
                raise_error(f'y preprocess must be a string or a '
                            'valid sklearn transformer instance')
    return preprocess_y


def prepare_cv(cv):
    """Generates an CV using string compatible with
    repeat:5_nfolds:5 where 5 can be exchange with any int.
    Alternatively, it can take in a valid cv splitter or int as
    in cross_validate in sklearn.

    Parameters
    ----------
    cv : int or str or cv_splitter
        [description]

    """

    def parser(cv_string):
        n_repeats, n_folds = cv_string.split('_')
        n_repeats, n_folds = [int(name.split(':')[-1])
                              for name in [n_repeats, n_folds]]
        logger.info(f'CV interpeted as RepeatedKFold with {n_repeats} '
                    f'repetitions of {n_folds} folds')
        return RepeatedKFold(n_splits=n_folds, n_repeats=n_repeats)

    try:
        _cv = check_cv(cv)
        logger.info(f'Using scikit-learn CV scheme {_cv}')
    except ValueError:
        _cv = parser(cv)

    return _cv


def prepare_scoring(estimator, score_name):
    return get_extended_scorer(estimator, score_name)


def _create_preprocess_tuple(transformer):
    if type(transformer) == list:
        return transformer
    elif type(transformer) == str:
        trans_name = transformer
        trans, returned_features = get_transformer(transformer)
    else:
        trans_name = transformer.__class__.__name__.lower()
        trans = clone(transformer)
        returned_features = 'unknown'

    transform_columns = (['continuous', 'confound']
                         if trans_name == 'remove_confound'
                         else 'continuous')

    return trans_name, trans, returned_features, transform_columns


def _is_valid_sklearn_transformer(transformer):

    return (hasattr(transformer, 'fit') and
            hasattr(transformer, 'transform') and
            hasattr(transformer, 'get_params') and
            hasattr(transformer, 'set_params'))


def _is_valid_sklearn_model(model):
    return (hasattr(model, 'fit') and
            hasattr(model, 'predict') and
            hasattr(model, 'get_params') and
            hasattr(model, 'set_params'))


def check_consistency(
        pipeline, preprocess_X, preprocess_y, preprocess_confounds, df_X_conf,
        y, cv, groups, problem_type):
    """Check the consistency of the parameters/input"""

    # Check problem type and the target.
    n_classes = np.unique(y.values).shape[0]
    if problem_type == 'binary_classification':
        # If not exactly two classes:
        if n_classes != 2:
            if preprocess_y is None:
                raise_error(
                    f'The number of classes ({n_classes}) is not suitable for '
                    'a binary classification. You can either specify '
                    '``pos_labels``, a suitable y transformer or change the '
                    'problem type.')
            else:
                warn(
                    f'The number of classes ({n_classes}) is not suitable for '
                    'a binary classification. However, a y transformer has '
                    'been set.')
    elif problem_type == 'multiclass_classification':
        if n_classes == 2:
            warn(
                f'A multiclass classification will be performed but only 2 '
                'classes are defined in y.')
    else:
        # Regression
        is_numeric = np.issubdtype(y.values.dtype, np.number)
        if not is_numeric:
            if preprocess_y is None:
                raise_error(
                    f'The kind of values in y ({y.values.dtype}) is not '
                    'suitable for a regression. You can either specify a '
                    'suitable y transformer or change the problem type.')
            else:
                warn(
                    f'The kind of values in y ({y.values.dtype}) is not '
                    'suitable for a regression. However, a y transformer has '
                    'been set.')
        else:
            n_classes = np.unique(y.values).shape[0]
            if n_classes == 2:
                warn(
                    f'A regression will be performed but only 2 '
                    'distinct values are defined in y.')
    # Check groups and CV scheme
    if groups is not None:
        valid_instances = (
            model_selection.GroupKFold,
            model_selection.GroupShuffleSplit,
            model_selection.LeaveOneGroupOut,
            model_selection.LeavePGroupsOut
        )
        if not isinstance(cv, valid_instances):
            warn('The parameter groups was specified but the CV strategy '
                 'will not consider them.')