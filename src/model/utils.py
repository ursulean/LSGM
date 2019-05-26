from pathlib import Path
import os
import numpy as np
import torch
import torch.nn.functional as F
from torch.distributions import Gumbel

gumbel = Gumbel(0, 1)


def my_softmax(input, axis=1):
    # From https://github.com/ethanfetaya/NRI/blob/master/utils.py
    trans_input = input.transpose(axis, 0).contiguous()
    soft_max_1d = F.softmax(trans_input)
    return soft_max_1d.transpose(axis, 0)


def encode_onehot(labels):
    """
    From https://github.com/ethanfetaya/NRI/blob/master/utils.py
    :param labels: 
    :return: 
    """

    classes = set(labels)
    classes_dict = {c: np.identity(len(classes))[i, :] for i, c in
                    enumerate(classes)}
    labels_onehot = np.array(list(map(classes_dict.get, labels)),
                             dtype=np.int32)

    return labels_onehot


def gen_fully_connected(n_elements, device=None):
    # From https://github.com/ethanfetaya/NRI/blob/master/utils.py
    # Generate off-diagonal interaction graph
    off_diag = np.ones([n_elements, n_elements]) - np.eye(n_elements)

    rel_rec = np.array(encode_onehot(np.where(off_diag)[1]), dtype=np.float32)
    rel_send = np.array(encode_onehot(np.where(off_diag)[0]), dtype=np.float32)
    rel_rec = torch.FloatTensor(rel_rec)
    rel_send = torch.FloatTensor(rel_send)

    if device:
        rel_rec, rel_send = rel_rec.to(device), rel_send.to(device)

    return rel_rec, rel_send


def node2edge(m, adj_rec=None, adj_send=None):
    """
    Calculates edge embeddings
    :param m: Tensor with shape (SAMPLES, OBJECTS, FEATURES)
    :param adj_rec:
    :param adj_send:
    :return:
    """
    outgoing = torch.matmul(adj_send, m)
    incoming = torch.matmul(adj_rec, m)
    return torch.cat([outgoing, incoming], dim=2)


def edge2node(m, adj_rec, adj_send):
    """
    Performs accumulation of message passing by summing over connected edges for each node
    :param x: tensor with shape (N_OBJ, N_HIDDEN)
    :param adj_rec:
    :param adj_send:
    :return:
    """
    incoming = torch.matmul(adj_rec.t(), m)
    return incoming / incoming.size(1)


def sample_gumbel(shape):
    gumbel.sample(shape)


def load_models(enc: torch.nn.Module, dec: torch.nn.Module, config: dict):
    models_path = config['training']['load_path']
    path = Path(models_path).parent / "models"

    # Find different models for each epoch
    max_epoch = -1
    for f in os.listdir(path):
        epoch = int(f.split("_epoch")[-1].split(".pt")[0])
        max_epoch = max(epoch, max_epoch)

    if max_epoch == -1:
        raise FileNotFoundError(f"No models found under {models_path}")

    encoder_file = path / f"encoder_epoch{max_epoch}.pt"
    decoder_file = path / f"decoder_epoch{max_epoch}.pt"
    enc.load_state_dict(torch.load(encoder_file))
    dec.load_state_dict(torch.load(decoder_file))

    print(f"Loaded encoder {encoder_file} and decoder {decoder_file}")
    config['training']['load_path'] = None
    return enc, dec


def nll():
    pass


def kl():
    pass
