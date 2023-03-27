from kgcnn.data.datasets.OCHEMDataset2023 import OCHEMDataset2023


class OCHEMDataset(OCHEMDataset2023):
    r"""Store and process 'OCHEM' dataset from `OCHEM`_ application.
    """

    def __init__(self, reload=False, verbose: int = 10):
        r"""Initialize OCHEM dataset.

        Args:
            reload (bool): Whether to reload the data and make new dataset. Default is False.
            verbose (int): Print progress or info for processing where 60=silent. Default is 10.
        """
        super(OCHEMDataset, self).__init__("OCHEM", reload=reload, verbose=verbose)
