import os
import pathlib
import json
import pickle
import requests
import numpy as np
import pandas as pd
from typing import Optional, Union
from scipy.special import expit
from . import logger

#create ~/.celltypist and subdirs
celltypist_path = os.path.join(str(pathlib.Path.home()), '.celltypist')
pathlib.Path(celltypist_path).mkdir(parents=True, exist_ok=True)
data_path = os.path.join(celltypist_path, "data")
models_path = os.path.join(data_path, "models")
pathlib.Path(models_path).mkdir(parents=True, exist_ok=True)


class Model():
    """
    Class that wraps the logistic Classifier and the StandardScaler.

    Parameters
    ----------
    clf
        A logistic Classifier incorporated in the loaded model.
    scaler
        A StandardScaler incorporated in the loaded model.
    description
        Description of the model as a dictionary.

    Attributes
    ----------
    classifier
        The logistic Classifier incorporated in the loaded model.
    scaler
        The StandardScaler incorporated in the loaded model.
    description
        Description of the loaded model.
    """
    def __init__(self, clf, scaler, description):
        self.classifier = clf
        self.scaler = scaler
        self.description = description

    @staticmethod
    def load(model: Optional[str] = None):
        """
        Load the desired model.

        Parameters
        ----------
        model
            Model name specifying the model you want to load. Default to `Immune_All_Low.pkl` if not provided.
            To see all available models and their descriptions, use :func:`~celltypist.models.models_description()`.

        Returns
        ----------
        :class:`~celltypist.models.Model`
            A :class:`~celltypist.models.Model` object.
        """
        if not model:
            model = get_default_model()
        if model in get_all_models():
            model = get_model_path(model)
        if not os.path.isfile(model):
            raise FileNotFoundError(f"🛑 No such file: {model}")
        with open(model, "rb") as fh:
            try:
                pkl_obj = pickle.load(fh)
                return Model(pkl_obj['Model'], pkl_obj['Scaler_'], pkl_obj['description'])
            except Exception as exception:
                raise Exception(f"🛑 Invalid model: {model}. {exception}")

    @property
    def cell_types(self) -> np.ndarray:
        """Get cell types included in the model."""
        return self.classifier.classes_

    @property
    def features(self) -> np.ndarray:
        """Get genes included in the model."""
        return self.classifier.features

    def predict_labels_and_prob(self, indata, mode: str = 'best match', p_thres: float = 0.5) -> tuple:
        """
        Get the decision matrix, probability matrix, and predicted cell types for the input data.

        Parameters
        ----------
        indata
            The input array-like object used as a query.
        mode
            The way cell prediction is performed.
            For each query cell, the default (`best match`) is to choose the cell type with the largest score/probability as the final prediction.
            Setting to `prob match` will enable a multi-label classification, which assigns 0 (i.e., unassigned), 1, or >=2 cell type labels to each query cell.
            (Default: `best match`)
        p_thres
            Probability threshold for the multi-label classification. Ignored if `mode` is `best match`.
            (Default: 0.5)

        Returns
        ----------
        tuple
            A tuple of decision score matrix, raw probability matrix, and predicted cell type labels.
        """
        scores = self.classifier.decision_function(indata)
        probs = expit(scores)
        if mode == 'best match':
            return scores, probs, self.classifier.classes_[scores.argmax(axis=1)]
        elif mode == 'prob match':
            flags = probs > p_thres
            labs = np.array(['|'.join(self.classifier.classes_[np.where(x)[0]]) for x in flags])
            labs[labs == ''] = 'Unassigned'
            return scores, probs, labs
        else:
            raise ValueError(f"🛑 Unrecognized `mode` value, should be one of `best match` or `prob match`")

    def write(self, file: str) -> None:
        """Write out the model."""
        obj = dict(Model = self.classifier, Scaler_ = self.scaler, description = self.description)
        file = os.path.splitext(file)[0] + '.pkl'
        with open(file, 'wb') as output:
            pickle.dump(obj, output)

def get_model_path(file: str) -> str:
    """
    Get the full path to a file in the `models` folder.

    Parameters
    ----------
    file
        File name as a string.
        To see all available models and their descriptions, use :func:`~celltypist.models.models_description()`.

    Returns
    ----------
    str
        A string of the full path to the desired file.
    """
    return os.path.join(models_path, f"{file}")


def get_default_model() -> str:
    """
    Get the default model name.

    Returns
    ----------
    str
        A string showing the default model name (should be `Immune_All_Low.pkl`).
    """
    models_json = get_models_index()
    default_model = [m["filename"] for m in models_json["models"] if ("default" in m and m["default"])]
    if not default_model:
        first_model = models_json["models"][0]["filename"]
        logger.warn(f"👀 No model marked as 'default', using {first_model}")
        return first_model
    if len(default_model) > 1:
        logger.warn(f"👀 More than one model marked as 'default', using {default_model[0]}")
    return default_model[0]


def get_all_models() -> list:
    """
    Get a list of all the available models.

    Returns
    ----------
    list
        A list of available models.
    """
    download_if_required()
    available_models = []
    for model_filename in os.listdir(models_path):
        if model_filename.endswith(".pkl"):
            model_name = os.path.basename(model_filename)
            available_models.append(model_name)
    return available_models


def download_if_required() -> None:
    """Download models if there are none present in the `models` directory."""
    if len([m for m in os.listdir(models_path) if m.endswith(".pkl")]) == 0:
        logger.info(f"🔎 No available models. Downloading...")
        download_models()


def get_models_index(force_update: bool=False) -> dict:
    """
    Get the model json object containing the model list.

    Parameters
    ----------
    force_update
        If set to `True`, will download the latest model json file from the remote.
        (Default: `False`)

    Returns
    ----------
    dict
        A dict object converted from the model json file.
    """
    models_json_path = get_model_path("models.json")
    if not os.path.exists(models_json_path) or force_update:
        download_model_index()
    with open(models_json_path) as f:
        return json.load(f)


def download_model_index(only_model: bool = True) -> None:
    """
    Download the `models.json` file from the remote server.

    Parameters
    ----------
    only_model
        If set to `False`, will also download the models in addition to the json file.
        (Default: `True`)
    """
    url = 'https://celltypist.cog.sanger.ac.uk/models/models.json'
    logger.info(f"📜 Retrieving model list from server {url}")
    with open(get_model_path("models.json"), "wb") as f:
        f.write(requests.get(url).content)
    model_count = len(requests.get(url).json()["models"])
    logger.info(f"📚 Total models in list: {model_count}")
    if not only_model:
        download_models()

def download_models(force_update: bool=False, model: Optional[Union[str, list, tuple]] = None) -> None:
    """
    Download all the available or selected models.

    Parameters
    ----------
    force_update
        Whether to fetch a latest JSON index for downloading all available or selected models.
        Set to `True` if you want to parallel the latest celltypist model releases.
        (Default: `False`)
    model
        Specific model(s) to download. By default, all available models are downloaded.
        Set to a specific model name or a list of model names to only download a subset of models.
        For example, set to `["ModelA.pkl", "ModelB.pkl"]` to only download ModelA and ModelB.
        To check all available models, ues `celltypist.models.models_description(on_the_fly=False)`
    """
    models_json = get_models_index(force_update)
    logger.info(f"📂 Storing models in {models_path}")
    if len(model_list)!=0:
         logger.info(f"🔎 Filtering model list using {model_list}")
         models_json["models"] = [m for m in models_json["models"] if m["filename"] in model_list]
         if len(models_json["models"])==0:
             logger.error(f"🛑 All models filetered out. No match for {model_list}")
    model_count = len(models_json["models"])
    for idx,model in enumerate(models_json["models"]):
        model_path = get_model_path(model["filename"])
        if os.path.exists(model_path) and not force_update:
            logger.info(f"⏩ Skipping [{idx+1}/{model_count}]: {model['filename']} (file exists)")
            continue
        logger.info(f"💾 Downloading model [{idx+1}/{model_count}]: {model['filename']}")
        try:
            with open(model_path, "wb") as f:
                f.write(requests.get(model["url"]).content)
        except Exception as exception:
            logger.error(f"🛑 {model['filename']} failed {exception}")


def models_description(on_the_fly: bool=True) -> pd.DataFrame:
    """
    Get the descriptions of all available models.

    Parameters
    ----------
    on_the_fly
        Whether to fetch the model information from downloaded model files.
        If set to `False`, will fetch the information directly from the JSON file.
        (Default: `True`)

    Returns
    ----------
    :class:`~pandas.DataFrame`
        A :class:`~pandas.DataFrame` object with model descriptions.
    """
    if on_the_fly:
        filenames = get_all_models()
        descriptions = [Model.load(filename).description['details'] for filename in filenames]
    else:
        models_json = get_models_index()
        models = models_json["models"]
        filenames = [model['filename'] for model in models]
        descriptions = [model['details'] for model in models]
    return pd.DataFrame({'model': filenames, 'description': descriptions})
