import os
import json
from kgcnn.data.ochem import OCHEMDataset
from kgcnn.data.download import DownloadDataset

class OCHEMDataset2023(OCHEMDataset,DownloadDataset):
    r"""loader for `OCHEM` dataset.
    """
    datasets_load_info = {
        "OCHEM": {"dataset_name": "OCHEM", "data_directory_name": "OCHEM", "load_url" : 'data.csv'},
    }
    datasets_prepare_data_info = {
        "OCHEM": {"make_conformers": True, "add_hydrogen": True, "smiles_column_name": "smiles"},
    }
    datasets_read_in_memory_info = {
        "OCHEM": {"add_hydrogen": False, "has_conformers": False, "label_column_name": ""},
    }

    def __init__(self, dataset_name: str, reload: bool = False, verbose: int = 10):
        """Initialize a `OCHEMDataset2023` instance from string identifier.

        Args:
            dataset_name (str): Name of a dataset.
            dataset_file_name (str) : Name of the file
            reload (bool): Download the dataset again and prepare data on disk.
            verbose (int): Print progress or info for processing where 60=silent. Default is 10.
        """



        if not isinstance(dataset_name, str):
            raise ValueError("Please provide string identifier for TUDataset.")

        OCHEMDataset.__init__(self, verbose=verbose, dataset_name=dataset_name)

        # Prepare download
        if dataset_name in self.datasets_load_info:
            self.download_info = self.datasets_load_info[dataset_name]
            self.download_info.update({"load_url": "data.csv"})
        else:
            raise ValueError("Can not resolve '%s' as a Molecule. Pick: " % dataset_name,
                             self.datasets_load_info.keys(),
                             "For new dataset, add to `datasets_download_info` list manually.")

        # Load JSON parameters if provided
        #if json_params is not None:
        #    with open(json_params, 'r') as json_file:
        #        params = json.load(json_file)
        #        if 'load_url' in params:
        #            self.download_info.update({"load_url": params['load_url']})


        DownloadDataset.__init__(self, **self.download_info, reload=reload, verbose=verbose)

        self.data_directory = os.path.join(self.data_main_dir, self.data_directory_name)
        self.file_name = self.load_url
        self.dataset_name = dataset_name
        self.require_prepare_data = True
        self.fits_in_memory = True

        if self.require_prepare_data:
            self.prepare_data(overwrite=reload, **self.datasets_prepare_data_info[self.dataset_name])
        if self.fits_in_memory:
            self.read_in_memory(**self.datasets_read_in_memory_info[self.dataset_name])

