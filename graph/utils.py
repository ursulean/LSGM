import math
import random
from os import path as osp

import numpy as np
import torch
from torch_geometric import transforms as T
from torch_geometric.datasets import CoraFull, Coauthor, Planetoid, Reddit
from torch_geometric.nn.models.autoencoder import negative_sampling
from torch_geometric.utils import to_undirected
from tqdm import tqdm

from graph.datasets.snap import Amazon


def sparse_precision_recall(data, sparse_matrix, verbose=True):
    print("Compute Sparse-Precision-Recall")
    all_edges = extract_all_edges_from_graph_data(data)

    pred = sparse_matrix.coalesce().indices().t().detach().cpu().numpy()
    pred = set(zip(pred[:, 0], pred[:, 1]))
    true = set(zip(all_edges[:, 0], all_edges[:, 1]))

    print(f"Sparse Precision-Recall: {len(pred)} edges detected by LSH out of {len(true)} in total.")
    return evaluate_edges(pred, true, verbose)


def dense_precision_recall(data, dense_matrix, min_sim, verbose=True):
    if verbose:
        print("Compute Dense-Precision-Recall")

    all_edges = extract_all_edges_from_graph_data(data)
    pred = (dense_matrix.detach().cpu().numpy() > min_sim).nonzero()

    pred = set(zip(pred[0], pred[1]))
    true = set(zip(all_edges[:, 0], all_edges[:, 1]))

    if verbose:
        print(f"Dense Precision-Recall: {len(pred)} edges detected out of {len(true)} in total.")

    return evaluate_edges(pred, true, verbose)


def sampled_dense_precision_recall(data, sampled_dense_matrix, ix_mapping, min_sim, verbose=True):
    if verbose:
        print("Compute Sampled Dense-Precision-Recall")

    embedding_indices = set(ix_mapping.values())
    relevant_edges = []

    for edge in extract_all_edges_from_graph_data(data):
        if edge[0] in embedding_indices and edge[1] in embedding_indices:
            relevant_edges.append(edge)

    relevant_edges = np.array(relevant_edges)

    pred = (sampled_dense_matrix.detach().cpu().numpy() > min_sim).nonzero()

    # Apply index transformation to the retrieved indices
    pred = np.vectorize(ix_mapping.get)(pred)

    pred = set(zip(pred[0], pred[1]))
    true = set(zip(relevant_edges[:, 0], relevant_edges[:, 1]))

    if verbose:
        print(f"Dense Precision-Recall: {len(pred)} edges detected out of {len(true)} in total.")

    return evaluate_edges(pred, true, verbose)

    # Must remove non-relevant indices from all_edges. Might also try to multiply recall by len(data)/len(ix_mapping) to re-normalize it.


def sparse_v_dense_precision_recall(dense_matrix, sparse_matrix, min_sim, verbose=True):
    """
    Compares Sparse-Adjacency matrix (LSH-Version) to the Dense-Adjacency matrix (non-LSH-version), which serves as GT.
    :param dense_matrix:
    :param sparse_matrix:
    :param min_sim_percentile:
    :return:
    """

    dense_pred = (dense_matrix.detach().cpu().numpy() > min_sim).nonzero()
    dense_pred = set(zip(dense_pred[0], dense_pred[1]))

    sparse_pred = sparse_matrix.coalesce().indices().t().detach().cpu().numpy()
    sparse_pred = set(zip(sparse_pred[:, 0], sparse_pred[:, 1]))

    print(f"LSH found {len(sparse_pred)} edges out of {len(dense_pred)} edges that the naive version predicted.")
    return evaluate_edges(sparse_pred, dense_pred, verbose)


def evaluate_edges(pred, true, verbose=True):
    """
    Calculates precision and recall for the predicted connections
    :return: (precision, recall)-tuple
    """
    sum = 0.0

    prec_loop = pred if not verbose else tqdm(pred, desc="Checking precision")

    for conn in prec_loop:
        if conn in true:
            sum += 1.0

    precision = (sum / len(pred)) if len(pred) != 0 else 0

    sum = 0.0

    rec_loop = true if not verbose else tqdm(true, desc="Checking recall")

    for conn in rec_loop:
        if conn in pred:
            sum += 1.0

    recall = (sum / len(true)) if len(pred) != 0 else 0
    return precision, recall


def extract_all_edges_from_graph_data(data):
    return torch.cat((data.val_pos_edge_index,
                      data.test_pos_edge_index,
                      data.train_pos_edge_index), 1).t().detach().cpu().numpy()


def sample_percentile(q, matrix_or_embeddings, dist_measure=None, sigmoid=False, sample_size=1000):
    """
    Samples from the given tensor to estimate a percentile.
    :param q: The percentile to look for the corresponding value in the pairs. In [0, 1]
    :param matrix_or_embeddings: As the name suggests, this param can either be the dense (N, N) adjacency matrix with values already computed, or the (N, D) matrix of embeddings.
    :param dist_measure: If given the matrix of embeddings, the distances must be computed directly in this function
    :param sigmoid: Whether to sigmoid computed distances. Only valid if embeddings are given.
    """

    if isinstance(q, (list, tuple, np.ndarray)):
        q = np.array(q)
        assert np.all((q >= 0.0) & (q <= 1.0)), "Invalid value inside q"
    else:
        assert q <= 1.0 and q >= 0.0, "Invalid value for q"

    assert isinstance(matrix_or_embeddings, torch.Tensor), "matrix_or_embeddings is not a torch Tensor."

    N_1, N_2 = matrix_or_embeddings.shape
    sample_size = min(sample_size, int(N_1))
    sample_ix = np.random.choice(np.arange(N_1), size=sample_size, replace=False)
    #     sample_ix_a = np.random.choice(np.arange(N_1), size=sample_size, replace=False)
    #     sample_ix_b = np.random.choice(np.arange(N_1), size=sample_size, replace=False)

    # Matrix case
    if N_1 == N_2:
        sample_distances = matrix_or_embeddings[np.ix_(sample_ix, sample_ix)].detach()

    # Embeddings case
    else:
        assert N_1 > N_2, "Dimensions of embeddings bigger than n_nodes, something might be wrong."
        assert dist_measure in ['cosine', 'dot'], "dist_measure must be set as 'cosine' or 'dot'"

        # sample_a, sample_b = matrix_or_embeddings[sample_ix_a].detach(), matrix_or_embeddings[sample_ix_b].detach()
        sample_embeddings = matrix_or_embeddings[sample_ix].detach()

        # If cosine just normalize vectors
        if dist_measure == 'cosine':
            sample_embeddings /= torch.norm(sample_embeddings, dim=1)[:, None]
        #             sample_a /= torch.norm(sample_a, dim=1)[:, None]
        #             sample_b /= torch.norm(sample_b, dim=1)[:, None]

        sample_distances = torch.mm(sample_embeddings, sample_embeddings.t())

        if sigmoid:
            sample_distances = torch.sigmoid(sample_distances)

    return np.percentile(sample_distances.cpu().numpy(), q * 100), sample_ix


def load_data(dataset_name):
    """
    Loads required data set and normalizes features.
    Implemented data sets are any of type Planetoid and Reddit.
    :param dataset_name: Name of data set
    :return: Tuple of dataset and extracted graph
    """
    path = osp.join(osp.dirname(osp.realpath(__file__)), '..', 'data', dataset_name)

    if dataset_name == 'cora_full':
        dataset = CoraFull(path, T.NormalizeFeatures())
    elif dataset_name.lower() == 'coauthor':
        dataset = Coauthor(path, 'Physics', T.NormalizeFeatures())
    elif dataset_name.lower() == 'reddit':
        dataset = Reddit(path, T.NormalizeFeatures())
    elif dataset_name.lower() == 'amazon':
        dataset = Amazon(path)
    else:
        dataset = Planetoid(path, dataset_name, T.NormalizeFeatures())

    print(f"Loading data set {dataset_name} from: ", path)
    data = dataset[0]  # Extract graph
    return dataset, data


def split_edges(data, val_ratio=0.05, test_ratio=0.1):
    r"""Splits the edges of a :obj:`torch_geometric.data.Data` object
    into positve and negative train/val/test edges.

    Args:
        data (Data): The data object.
        val_ratio (float, optional): The ratio of positive validation
            edges. (default: :obj:`0.05`)
        test_ratio (float, optional): The ratio of positive test
            edges. (default: :obj:`0.1`)
    """

    assert 'batch' not in data  # No batch-mode.

    row, col = data.edge_index

    # Return upper triangular portion.
    mask = row < col
    row, col = row[mask], col[mask]

    n_v = int(math.floor(val_ratio * row.size(0)))
    n_t = int(math.floor(test_ratio * row.size(0)))

    # Positive edges.
    perm = torch.randperm(row.size(0))
    row, col = row[perm], col[perm]

    r, c = row[:n_v], col[:n_v]
    data.val_pos_edge_index = torch.stack([r, c], dim=0)
    r, c = row[n_v:n_v + n_t], col[n_v:n_v + n_t]
    data.test_pos_edge_index = torch.stack([r, c], dim=0)

    r, c = row[n_v + n_t:], col[n_v + n_t:]
    data.train_pos_edge_index = torch.stack([r, c], dim=0)
    data.train_pos_edge_index = to_undirected(data.train_pos_edge_index)

    # Negative edges.
    num_nodes = data.num_nodes
    # neg_adj_mask = torch.ones(num_nodes, num_nodes, dtype=torch.uint8)
    # neg_adj_mask = neg_adj_mask.triu(diagonal=1)
    # neg_adj_mask[row, col] = 0

    # neg_row, neg_col = neg_adj_mask.nonzero().t()
    neg_row, neg_col = negative_sampling(data.test_pos_edge_index, num_nodes)

    perm = random.sample(
        range(neg_row.size(0)), min(n_v + n_t, neg_row.size(0)))
    perm = torch.tensor(perm)
    perm = perm.to(torch.long)
    neg_row, neg_col = neg_row[perm], neg_col[perm]

    row, col = neg_row[:n_v], neg_col[:n_v]
    data.val_neg_edge_index = torch.stack([row, col], dim=0)

    row, col = neg_row[n_v:n_v + n_t], neg_col[n_v:n_v + n_t]
    data.test_neg_edge_index = torch.stack([row, col], dim=0)

    data.edge_index = None

    return data
