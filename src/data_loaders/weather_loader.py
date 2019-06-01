import os
import glob
import torch
import numpy as np
import pandas as pd
from random import shuffle
from os.path import join, dirname

from torchvision import transforms, utils
from torch.utils.data import Dataset, DataLoader

class WeatherDataset(Dataset):
    """
    Wrapper class for the Spain weather dataset
    """
    def __init__(self, n_samples, n_nodes, n_timesteps, features, filename=None, force_new=False, discard=False):
        """
        Generates the dataset with the given parameters, unless a similar dataset has been generated
            before, in which case it is loaded from the file.
        Args:
            n_samples(int): Number of simulations to fetch
            n_nodes(int): Number of atoms in the simulation
            n_timesteps(int): Number of timesteps in each simulation
            features(list(str)): The list of feature names to track at each time step
            filename(str): The name of the file to save to/load from. If the file already exists,
                the data is loaded from it and checked whether it matches the required parameters,
                unless the force_new parameter is set, in which case it is overwritten anyway
            force_new(boolean, optional): Force generation of a new set of simulations,
                instead of using already existing ones from a file.
            discard(boolean, optional): Whether to discard the generated data or to save
                it, useful for debugging. Does not apply if filename is specified
        """
        
        self.n_samples = n_samples
        self.n_nodes = n_nodes
        self.n_timesteps = n_timesteps
        self.features = features
        
        self.generate_dset(filename, force_new, discard)
        assert self.dset.shape == (self.n_samples, self.n_nodes, self.n_timesteps, len(self.features)), "Dataset dimensions do not match specifications"
        
        
    def __len__(self):
        return len(self.dset)

    def __getitem__(self, idx):
        return self.dset[idx]
    
    def generate_dset(self, filename, force_new, discard):
        """
        Actual meat and potatoes
        """
        
        module_dir = dirname(os.path.abspath(__file__))
        data_dir = join(dirname(dirname(module_dir)), "datasets", "weather")
                
        # .npy file name convention if filename is not given:
        # #samples_#nodes_#timesteps_#features_#filesOfSameCharacteristics.npy
        
        # Whether the file in "filename" or some unnamed file with similar parameters already exists
        already_exists = False
        
        # This block searches for existing files with a matching filename or dataset generation parameters
        if filename is None:
        
            npy_files = glob.glob(join(data_dir, "[0-9]*.npy"))
            shuffle(npy_files)
            highest_index = -1

            # Check all existing .npy files for matching dataset generation parameters
            for f in npy_files:
                fname = os.path.basename(f).split(".")[0]
                spl = fname.split("_")
                assert len(spl)==5, ".npy filename error"

                if int(spl[0]) >= self.n_samples and int(spl[1]) == self.n_nodes and int(spl[2]) == self.n_timesteps and int(spl[3]) == len(self.features):

                    already_exists = True
                    fpath = f
                    if int(spl[4]) > highest_index:
                        highest_index = int(spl[4])
                
        else:
            if os.path.isfile(join(data_dir, filename)):
                already_exists = True
                fpath = join(data_dir, filename)

        
        # Whether to create a new file or use an existing one, if available
        if already_exists and not force_new:
            self.dset = np.load(fpath, allow_pickle=True)[:self.n_samples]
            assert self.dset.shape == (self.n_samples, self.n_nodes, self.n_timesteps, len(self.features)), "Given file name contains dataset of a different shape than specified in parameters."
        
        else:
            
            all_files = glob.glob(join(data_dir, "*.csv"))
            data = pd.concat((pd.read_csv(f) for f in all_files), ignore_index=True)
            self.dset, _ = self.sample_configurations(data, self.n_samples, self.n_nodes, self.n_timesteps, self.features)
            
            save_location = join(data_dir, filename) if filename is not None else join(data_dir,
                                                                                        str(self.n_samples) + "_"
                                                                                        + str(self.n_nodes) + "_"
                                                                                        + str(self.n_timesteps) + "_"
                                                                                        + str(len(self.features)) + "_"
                                                                                        + str(highest_index+1) + ".npy")
            if not discard:
                np.save(save_location, self.dset)
            
    # The following functions can potentially be made into abstract methods, or made to work directly on self. variables
    
    def remove_deep(self, src, matching_df, verbose=False):
        """
        For each row in matching_dataframe, this function removes all matching rows in src.
        Assumes that matching_df only contains colums which we want to match against.
        Args:
            src (pandas DataFrame): The source DataFrame that rows will be removed from
            matching_df (pandas DataFrame): The target DataFrame containing rows with
                dates that will be removed from src
            verbose(boolean): Whether to display progress and auxilliary information
        """
        src.reset_index(inplace=True, drop=True)

        original_len = len(src)
        
        # Avoid extra work and later division by 0
        if original_len == 0:
            return 0
        
        src_dates = src[['year','month','day']]
        trg_dates = matching_df[['year','month','day']].drop_duplicates()
        i=0
        indexNames = pd.Index([])
        
        for index, row in trg_dates.iterrows():
            indexNames = indexNames.union(src_dates[(src_dates['year'] == row['year'])
                                                    & (src_dates['month'] == row['month'])
                                                    & (src_dates['day'] == row['day'])].index)
            i+=1
            if i%100==0 and verbose:
                print("{}/{}".format(i, len(trg_dates)))

        src.drop(indexNames , inplace=True)
        src.reset_index(inplace=True, drop=True)
        reduction_percentage = (1-(len(src)/original_len))*100

        if verbose:
            print("Finished. {}% Reduction in size".format(reduction_percentage))

        return reduction_percentage

    def align_dates(self, df, features):
        """
        Aligns all stations in a given dataframe by date, by removing all entries for dates
            for which at least one weather station's measurements are missing
        Args:
            df (pandas DataFrame): The source DataFrame that rows will be removed from
            features(list(str)): The list of features which should be aligned in the time dimension
        """
        n_nodes = len(df['station_id'].unique())
        all_present = (df.groupby(['year','month','day']).count()[features]==n_nodes).all(axis=1)
        ix_removal = all_present[all_present==False].index
        dates_removal = ix_removal.to_frame().reset_index(drop=True)

        self.remove_deep(df, dates_removal)


    def get_configuration(self, df, conf):
        """
        Fetches a subset of the original dataset, which only contains the given configuration
            of weather stations, for which the entries that are removed are only those which
            create a conflict with this specific configuration of stations.
        Args:
            df (pandas DataFrame): The source DataFrame that contains all of the (dirty) data
            conf(list(str)): The list weather station IDs which constitute the configuration.
        """
        # Get timesteps for rows matching configuration, remove null values
        df_conf = df[df['station_id'].isin(conf)].reset_index(drop=True)
        missing_rows = df_conf[df_conf['avg_temp'].isna() | df_conf['rainfall'].isna()]
        self.remove_deep(df_conf, missing_rows)

        # Remove non-numerical values
        null_vals = (pd.to_numeric(df_conf['rainfall'], errors='coerce').isnull())
        null_indices = null_vals[null_vals==True].index
        self.remove_deep(df_conf, df_conf.loc[null_indices])

        # Remove timesteps for which not all stations have data    
        self.align_dates(df_conf, ['avg_temp','rainfall'])

        return df_conf

    def sample_configurations(self, df, n_samples, n_nodes, n_timesteps, features, threshold=3):
        """
        Samples different combinations of weather stations from the pool of candidates, and selectively
            applies the propagated deletion operations specified above for each sample, so as to get all
            possible simulations from each.
        Args:
            n_samples(int): Number of simulations to fetch
            n_nodes(int): Number of atoms in the simulation
            n_timesteps(int): Number of timesteps in each simulation
            features(list(str)): The list of feature names to track at each time step
            threshold(int): Tolerance for consecutive missing dates before splitting into separate groups.
        """
        
        # Add column in datetime format, should standardize to use this everywhere in this file
        # but it is a time-consuming process which doesn't have much benefit outside of elegance.
        if 'date' not in df.columns:
            df['date'] = pd.to_datetime(df[['year','month','day']])
        
        candidates = np.array(df['station_id'].unique())
        
        counter = df.groupby('station_id').count()

        # The combinations of stations that we choose to go with
        configurations = []

        # The number of 'simulations' generated so far
        current_samples = 0
        
        sample_df_size = n_timesteps * n_nodes

        # Numpy format dataset
        dataset = np.empty((0, n_nodes, n_timesteps, len(features)))

        while current_samples < n_samples:
            sample = np.random.choice(candidates, size=n_nodes, replace=False)
                        
            # Skip iteration if we come across a duplicate (1/75287520 chance for 5 nodes)
            if self.nested_list_member(configurations, sample):
                continue
            
            df_conf = self.get_configuration(df, sample)
                        
            # If all stations made it through the deletion process, and there are enough time steps for >=1 sample
            if len(df_conf['station_id'].unique()) == n_nodes and len(df_conf) >= sample_df_size:
                
                # Sort values by date
                df_conf = df_conf.sort_values(by='date')
                
                # Turn datetime objects into ordinal values in order to easily find consecutive clusters
                ordinal_dates = df_conf['date'].apply(pd.datetime.toordinal)
                
                # Group values into quasi-consecutive clusters (up to a threshold specified in function param "threshold")
                # Here we do not cluster unique dates, but rows in the dataframe sorted by date, for which there are n_nodes for
                # each unique date. These naturally get clustered into the same bucket
                consecutive = (ordinal_dates.diff() > threshold).astype('int').cumsum()
                grouped = consecutive.groupby(consecutive)
                
                clusters_indices = []
                                
                # Choose consecutive groups which are longer than the number of timesteps required for each sample
                for g in grouped.groups:
                    group_indices = grouped.get_group(g).index
                    if len(group_indices) >= sample_df_size:
                        clusters_indices.append(group_indices)
                                                
                for cluster in clusters_indices:
                    
                    # Determine the number of whole simulations we can use
                    
                    cluster_df = df_conf.loc[cluster].reset_index(drop=True)
                    
                    samples_in_cluster = len(cluster_df) // (sample_df_size)
                    
                    cluster_df = cluster_df.head(samples_in_cluster * sample_df_size)
                    
                    # Group by station id
                    grouped = cluster_df.groupby('station_id')[features]

                    # Convert to numpy array with different groups in different dimensions                
                    groups_np = np.array([grouped.get_group(s_id).values for s_id in sample], dtype=np.float32)
                    
                    # Determine whether we need to use less samples than available, due to reaching size limit
                    samples_to_use = samples_in_cluster if current_samples + samples_in_cluster <= n_samples else n_samples - current_samples
                    
                    current_samples += samples_to_use
                    configurations.append(sample)
                    
                    # Split numpy array by timesteps into different 'simulations'
                    groups_np = np.array(np.split(groups_np, samples_in_cluster, axis=1))
                    
                    # Sample simulations randomly if we need to lower the number due to reaching the required amount of samples in the dataset
                    if samples_to_use < samples_in_cluster:
                        sampled_simulations = np.random.choice(np.arange(samples_in_cluster), size=samples_to_use, replace=False)
                        groups_np = groups_np[sampled_simulations]

                    # Append to dataset array
                    dataset = np.append(dataset, groups_np, axis=0)
                

                    if current_samples == n_samples:
                        break

                print("Progress: {}/{}".format(current_samples, n_samples))
        
        return dataset, configurations

    def nested_list_member(self, nested_list, element):
        """
        Utility function used for checking membership of a sample in the nested list of configurations
            used so far. Necessary because the standard Python formulation of (elem in list) fails in
        Args:
            nested_list(list(list)): List containing list or np.array
            element(list): Check whether this list element is contained in the list of lists.
        """
        for lst in nested_list:
            if (lst==element).all():
                return True
        return False