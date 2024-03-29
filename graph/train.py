import argparse
import os
import os.path as osp
import pickle
import sys
import time

from torch_geometric.nn import GAE, VGAE

sys.path.append(osp.dirname(osp.dirname(osp.abspath(__file__))))

from graph.utils import *
from graph.early_stopping import EarlyStopping
from graph.modules import *
from graph.torch_lsh import LSHDecoder


def run_experiment(args):
    """
    Performing experiment for the given arguments
    """
    dataset, data = load_data(args.dataset)
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    # Define Model
    encoder = create_encoder(args.model, dataset.num_features, args.latent_dim).to(device)
    decoder = create_decoder(args.decoder).to(device)

    if args.model == 'GAE':
        model = GAE(encoder=encoder, decoder=decoder).to(device)
    else:
        model = VGAE(encoder=encoder, decoder=decoder).to(device)

    # Split edges of a torch_geometric.data.Data object into pos negative train/val/test edges
    # default ratios of positive edges: val_ratio=0.05, test_ratio=0.1
    print("Data.edge_index.size", data.edge_index.size(1))
    data = model.split_edges(data)
    node_features, train_pos_edge_index = data.x.to(device), data.train_pos_edge_index.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.01)

    def train_epoch():
        """
        Performing training over a single epoch and optimize over loss
        :return: log - loss of training loss
        """
        # Todo: Add logging of results

        model.train()
        optimizer.zero_grad()
        # Compute latent embedding Z
        latent_embeddings = model.encode(node_features, train_pos_edge_index)

        # Calculate loss and
        loss = model.recon_loss(latent_embeddings, train_pos_edge_index)
        if args.model in ['VGAE']:
            loss = loss + (1 / data.num_nodes) * model.kl_loss()

        # Compute gradients
        loss.backward()
        # Perform optimization step
        optimizer.step()

        # print("Train-Epoch: {} Loss: {}".format(epoch, loss))

        # ToDo: Add logging via Tensorboard
        log = {
            'loss': loss
        }

        return log

    def test(pos_edge_index, neg_edge_index):
        model.eval()
        with torch.no_grad():
            # compute latent var
            z = model.encode(node_features, train_pos_edge_index)

        # model.test return - AUC, AP
        return model.test(z, pos_edge_index, neg_edge_index)

    def test_naive_graph(z, sample_size=1000):

        if args.sample_dense_evaluation:
            graph_type = "sampled"
            z_sample, index_mapping = sample_graph(z, sample_size)
            t = time.time()
            adjacency = model.decoder.forward_all(z_sample, sigmoid=(args.decoder == 'dot'))
        else:
            graph_type = "full"
            t = time.time()
            adjacency = model.decoder.forward_all(z, sigmoid=(args.decoder == 'dot'))

        print(f"Computing {graph_type} graph took {time.time() - t} seconds.")
        print(
            f"Adjacency matrix takes {adjacency.element_size() * adjacency.nelement() / 10 ** 6} MB of memory.")

        if args.min_sim_absolute_value is None:
            args.min_sim_absolute_value, _ = sample_percentile(args.min_sim, adjacency, dist_measure=args.decoder,
                                                               sample_size=sample_size)

        if args.sample_dense_evaluation:
            precision, recall = sampled_dense_precision_recall(data, adjacency, index_mapping,
                                                               args.min_sim_absolute_value)
        else:
            precision, recall = dense_precision_recall(data, adjacency, args.min_sim_absolute_value)

        print("Predicted {} adjacency matrix has precision {} and recall {}!".format(graph_type, precision, recall))

        return precision, recall

    def sample_graph(z, sample_size):
        N, D = z.shape

        sample_size = min(sample_size, N)
        sample_ix = np.random.choice(np.arange(N), size=sample_size, replace=False)

        # Returns the sampled embeddings, and a mapping from their indices to the originals
        return z[sample_ix], {i: sample_ix[i] for i in np.arange(sample_size)}

    def test_compare_lsh_naive_graphs(z, assure_correctness=True):
        """

        :param z:
        :param assure_correctness:
        :return:
        """
        # Naive Adjacency-Matrix (Non-LSH-Version)
        t = time.time()
        # Don't use sigmoid in order to directly compare thresholds with LSH
        naive_adjacency = model.decoder.forward_all(z, sigmoid=(args.decoder == 'dot'))
        naive_time = time.time() - t
        naive_size = naive_adjacency.element_size() * naive_adjacency.nelement() / 10 ** 6

        if args.min_sim_absolute_value is None:
            args.min_sim_absolute_value, _ = sample_percentile(args.min_sim, z, dist_measure=args.decoder)

        print("______________________________Naive Graph Computation KPI____________________________________________")
        print(f"Computing naive graph took {naive_time} seconds.")
        print(f"Naive adjacency matrix takes {naive_size} MB of memory.")

        # LSH-Adjacency-Matrix:
        t = time.time()
        lsh_adjacency = LSHDecoder(bands=args.lsh_bands,
                                   rows=args.lsh_rows,
                                   verbose=True,
                                   assure_correctness=assure_correctness,
                                   sim_thresh=args.min_sim_absolute_value)(z)
        lsh_time = time.time() - t
        lsh_size = lsh_adjacency.element_size() * lsh_adjacency._nnz() / 10 ** 6

        print("__________________________________LSH Graph Computation KPI__________________________________________")
        print(f"Computing LSH graph took {lsh_time} seconds.")
        print(f"Sparse adjacency matrix takes {lsh_size} MB of memory.")

        print("________________________________________Precision-Recall_____________________________________________")
        # 1) Evaluation: Both Adjacency matrices against ground truth graph
        naive_precision, naive_recall = dense_precision_recall(data, naive_adjacency, args.min_sim_absolute_value)

        lsh_precision, lsh_recall = sparse_precision_recall(data, lsh_adjacency)

        print(f"Naive-Precision {naive_precision}; Naive-Recall {naive_recall}")
        print(f"LSH-Precision {lsh_precision}; LSH-Recall {lsh_recall}")

        print("_____________________________Comparison Sparse vs Dense______________________________________________")
        # 2) Evation: Compare both adjacency matrices against each other
        compare_precision, compare_recall = sparse_v_dense_precision_recall(naive_adjacency, lsh_adjacency,
                                                                            args.min_sim_absolute_value)
        print(
            f"LSH sparse matrix has {compare_precision} precision and {compare_recall} recall w.r.t. the naively generated dense matrix!")

        return naive_precision, naive_recall, naive_time, naive_size, lsh_precision, lsh_recall, lsh_time, lsh_size, compare_precision, compare_recall

    # Training routine
    early_stopping = EarlyStopping(args.use_early_stopping, patience=args.early_stopping_patience, verbose=True)

    logs = []

    if args.load_model and os.path.isfile("checkpoint.pt"):
        print("Loading model from savefile...")
        model.load_state_dict(torch.load("checkpoint.pt"))

    if not (args.load_model and args.early_stopping_patience == 0):
        for epoch in range(1, args.epochs):
            log = train_epoch()
            logs.append(log)

            # Validation metrics
            val_auc, val_ap = test(data.val_pos_edge_index, data.val_neg_edge_index)
            print('Validation-Epoch: {:03d}, AUC: {:.4f}, AP: {:.4f}'.format(epoch, val_auc, val_ap))

            # Stop training if validation scores have not improved
            early_stopping(val_ap, model)
            if early_stopping.early_stop:
                print("Applying early-stopping")
                break
    else:
        epoch = 0

    # Load best encoder
    print("Load best model for evaluation.")
    model.load_state_dict(torch.load('checkpoint.pt'))
    print("__________________________________________________________________________")
    # Training is finished, calculate test metrics
    test_auc, test_ap = test(data.test_pos_edge_index, data.test_neg_edge_index)
    print('Test Results: {:03d}, AUC: {:.4f}, AP: {:.4f}'.format(epoch, test_auc, test_ap))

    # Check if early stopping was applied or not - if not: model might not be done with training
    if args.epochs == epoch + 1:
        print("Model might need more epochs - Increase number of Epochs!")

    # Evaluate full graph
    latent_embeddings = model.encode(node_features, train_pos_edge_index)

    # Save embeddings to embeddings folder if flag is set
    if args.save_embeddings:
        embeddings_folder = osp.join(osp.dirname(osp.abspath(__file__)), 'embeddings')
        if not osp.isdir(embeddings_folder):
            os.makedirs(embeddings_folder)

        torch.save(latent_embeddings, osp.join(embeddings_folder, args.dataset + "_" + args.decoder + ".pt"))

    if not args.lsh:
        # Compute precision recall w.r.t the ground truth graph
        graph_precision, graph_recall = test_naive_graph(latent_embeddings)
        del model
        del encoder
        del decoder
        torch.cuda.empty_cache()
    else:
        # Precision w.r.t. the generated graph
        naive_precision, naive_recall, naive_time, naive_size, lsh_precision, \
        lsh_recall, lsh_time, lsh_size, \
        compare_precision, compare_recall = test_compare_lsh_naive_graphs(
            latent_embeddings)

        del model
        del encoder
        del decoder
        torch.cuda.empty_cache()

        return {'args': args,
                'test_auc': test_auc,
                'test_ap': test_ap,
                'naive_precision': naive_precision,
                'naive_recall': naive_recall,
                'naive_time': naive_time,
                'naive_size': naive_size,
                'lsh_precision': lsh_precision,
                'lsh_recall': lsh_recall,
                'lsh_time': lsh_time,
                'lsh_size': lsh_size,
                'compare_precision': compare_precision,
                'compare_recall': compare_recall}

        # results = np.append(np_result_file, args.dataset, args.lsh_bands, args.lsh_rows)


def run_grid_search(args):
    print("Performing Grid-Search")
    # Creating unique Grid-Search Filename
    timestr = time.strftime("%Y-%m-%d_%H-%M-%S")
    results_folder = osp.join(osp.dirname(osp.abspath(__file__)), 'results', timestr)
    if not osp.isdir(results_folder):
        os.makedirs(results_folder)
    # We don't need to run grid search over all datasets, but for each
    # dataset because they likely have different optimal hyperparams
    datasets = ["Cora", "PubMed", "CiteSeer", "Coauthor"]
    distance_measures = ['cosine']
    # Grid-Search Parameters
    percentiles = [0.97, 0.98, 0.995]
    lsh_bands = [32, 16, 8]
    lsh_rows = [16, 32, 64, 128, 196]

    for dset in datasets:
        train_from_scratch = True
        args.dataset = dset
        for percentile in percentiles:
            args.min_sim = percentile
            for dist in distance_measures:
                args.min_sim_absolute_value = None
                args.decoder = dist
                for bands in lsh_bands:
                    args.lsh_bands = bands
                    for rows in lsh_rows:
                        args.lsh_rows = rows
                        print("Performing combination: ", args.dataset, args.decoder, args.lsh_bands, args.lsh_rows,
                              args.min_sim)

                        if train_from_scratch:
                            args.load_model = False

                            args.early_stopping_patience = 100

                            # Training logic still takes most recent model that improved val error even with
                            # use_early_stopping=False, it just doesn't stop after x stagnations
                            args.use_early_stopping = True

                        else:
                            args.load_model = True
                            args.use_early_stopping = True
                            args.early_stopping_patience = 0

                        results = run_experiment(args)
                        train_from_scratch = False

                        print("_______________________________Store Results______________________________")

                        filename = osp.join(results_folder,
                                            "GS_" + dset +
                                            "_" + dist +
                                            "_" + str(bands) +
                                            "_" + str(rows) +
                                            "_" + str(percentile) + ".pkl")

                        with open(filename, "wb") as f:
                            pickle.dump(results, f)
                        print("Stored Results\n\n")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()

    # Global
    parser.add_argument('--seed', type=int, default=42, help="Random seed")

    # Dataset
    parser.add_argument('--dataset', type=str, default='PubMed', help="Data Set Name",
                        choices=["PubMed", "Cora", "CiteSeer", "Coauthor", "Reddit", "Amazon"])

    # Training
    parser.add_argument('--epochs', type=int, default=500, help="Number of Epochs in Training")
    parser.add_argument('--lr', type=float, default=0.001, help="Learning Rate")
    # Early Stopping
    parser.add_argument('--use-early-stopping', action='store_true')
    parser.add_argument('--early-stopping-patience', type=int, default=100)

    # Model Specific
    parser.add_argument('--load-model', action='store_true', default=False,
                        help="Loads model from checkpoint if available")
    parser.add_argument('--model', type=str, default='VGAE', help="Specify Model Type", choices=['gae', 'vgae'])
    parser.add_argument('--latent-dim', type=int, default=16, help="Size of latent embedding.")

    # LSH
    parser.add_argument('--lsh', action='store_true', default=False, help="Use Local-Sensitivity-Hashing")
    parser.add_argument('--lsh-bands', type=int, default=8, help="Specify bands-parameter for LSH")
    parser.add_argument('--lsh-rows', type=int, default=64, help="Specify rows-parameter for LSH")
    parser.add_argument('--decoder', type=str, default='dot', help="Specify Decoder Type",
                        choices=['dot', 'cosine'])

    # Similarity-Threshold
    parser.add_argument('--min-sim', type=float, default=0.99,
                        help="Specify the min. similarity PERCENTILE threshold for both naive and LSH")
    parser.add_argument('--min-sim-absolute-value', type=float, default=None)
    parser.add_argument('--grid-search', action="store_true", default=False, help="Perform Grid-Search if selected")

    # Miscellaneous
    parser.add_argument('--save-embeddings', action="store_true",
                        help="Whether to store embeddings tensor in graph/embeddings")
    parser.add_argument('--sample-dense-evaluation', action="store_true",
                        help="Whether to use sampling in dense graph eval. Use when the full graph doesn't fit in the VRAM")

    args = parser.parse_args()

    if args.grid_search:
        run_grid_search(args)
    else:
        print("Performing Single-Experiment")
        run_experiment(args)
