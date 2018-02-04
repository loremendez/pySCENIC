# -*- coding: utf-8 -*-

import numpy as np
import pandas as pd
from itertools import repeat
from typing import Type, List, Tuple, Optional

from .rnkdb import RankingDatabase
from .genesig import GeneSignature, Regulome


def recovery(rnk: pd.DataFrame, total_genes: int, weights: np.ndarray, rank_threshold: int, auc_threshold: float) -> (np.ndarray, np.ndarray):
    """
    Calculate recovery curves and AUCs. This is the workhorse of the recovery algorithm.

    :param rnk: A dataframe containing the rank number of genes of interest. Columns correspond to genes.
    :param total_genes: The total number of genes ranked.
    :param weights: the weights associated with the selected genes.
    :param rank_threshold: The total number of ranked genes to take into account when creating a recovery curve.
    :param auc_threshold: The fraction of the ranked genome to take into account for the calculation of the
        Area Under the recovery Curve.
    :return: A tuple of numpy arrays. The first array contains the recovery curves (n_features/n_cells x rank_threshold),
        the second array the AUC values (n_features/n_cells).
    """
    assert 0 < rank_threshold < total_genes, \
        "Rank threshold must be an integer between 1 and {0:d}".format(total_genes)
    assert 0.0 < auc_threshold <= 1.0, "AUC threshold must be a fraction between 0.0 and 1.0"
    rank_cutoff = int(round(auc_threshold * total_genes))
    assert rank_cutoff <= rank_threshold, \
        "An AUC threshold of {0:f} corresponds to {1:d} top ranked genes/regions in the database. " \
        "Please increase the rank threshold or decrease the AUC threshold.".format(auc_threshold, rank_cutoff)

    features, genes, rankings = rnk.index.values, rnk.columns.values, rnk.values
    weights = np.insert(weights, len(weights), 0.0)

    # Calculate recovery curves.
    def calc_rcc(ranking, weights, total_genes, rank_threshold):
        curranking = np.append(ranking, total_genes)
        return np.cumsum(np.bincount(curranking, weights=weights)[:rank_threshold])
    # Apply along axis does not improve performance, only more readable code.
    rccs = np.apply_along_axis(calc_rcc, 1, rankings, weights, total_genes, rank_threshold)

    # Calculate AUC.
    maxauc = float(rank_cutoff * total_genes)
    aucs = rccs[:, :rank_cutoff].sum(axis=1) / maxauc

    return rccs, aucs


def enrichment4cells(rnk_mtx: pd.DataFrame, regulome: Regulome, rank_threshold: int = 5000, auc_threshold: float = 0.05) -> pd.DataFrame:
    """
    Calculate the enrichment of the regulome for the cells in the ranking dataframe.

    :param rnk_mtx: The ranked expression matrix (n_cells, n_genes).
    :param regulome: The regulome the assess for enrichment
    :param rank_threshold: The total number of ranked genes to take into account when creating a recovery curve.
    :param auc_threshold: The fraction of the ranked genome to take into account for the calculation of the
        Area Under the recovery Curve.
    :return:
    """
    assert rnk_mtx
    assert regulome

    total_genes = len(rnk_mtx.columns)
    rnk = rnk_mtx[rnk_mtx.columns.isin(regulome.genes)]
    weights = np.asarray([regulome[gene] for gene in rnk.columns.values])
    rccs, aucs = recovery(rnk, total_genes, weights, rank_threshold, auc_threshold)
    index = pd.MultiIndex.from_tuples(list(zip(rnk.index.values, repeat(regulome.transcription_factor))),
                                      names=["Cell", "Regulome"])
    return pd.DataFrame(index=index, data={"AUC": aucs})


def enrichment4features(rnkdb: Type[RankingDatabase], gs: Type[GeneSignature], rank_threshold: int = 5000, auc_threshold: float = 0.05) -> pd.DataFrame:
    """
    Calculate AUC and NES for all regulatory features in the supplied database using the genes of the give signature.

    :param rnkdb: The database.
    :param gs: The gene signature the assess for enrichment
    :param rank_threshold: The total number of ranked genes to take into account when creating a recovery curve.
    :param auc_threshold: The fraction of the ranked genome to take into account for the calculation of the
        Area Under the recovery Curve.
    :return: A dataframe containing all information.
    """
    assert rnkdb, "A database must be supplied"
    assert gs, "A gene signature must be supplied"

    # Load rank of genes from database.
    df = rnkdb.load(gs)
    features = df.index.values
    genes = df.columns.values
    rankings = df.values
    weights = np.asarray([gs[gene] for gene in genes])

    rccs, aucs = recovery(df, rnkdb.total_genes, weights, rank_threshold, auc_threshold)
    ness = (aucs - aucs.mean()) / aucs.std()

    # The creation of a dataframe is a severe performance penalty.
    df_nes = pd.DataFrame(index=features,
                          data={("Enrichment", "AUC"): aucs, ("Enrichment", "NES"): ness})
    df_rnks = pd.DataFrame(index=features,
                           columns=list(zip(repeat("Ranking"), genes)),
                           data=rankings)
    df_rccs = pd.DataFrame(index=features,
                           columns=list(zip(repeat("Recovery"), np.arange(rank_threshold))),
                           data=rccs)
    return pd.concat([df_nes, df_rccs, df_rnks], axis=1)


def leading_edge(rcc: np.ndarray, avg2stdrcc: np.ndarray,
                 ranking: np.ndarray, genes: np.ndarray,
                 signature: Optional[Type[GeneSignature]] = None) -> List[Tuple[str,float]]:
    """
    Calculate the leading edge for a given recovery curve.

    :param rcc: The recovery curve.
    :param avg2stdrcc: The average + 2 standard deviation recovery curve.
    :param ranking: The rank numbers of the gene signature for a given regulatory feature.
    :param genes: The genes corresponding to the ranking available in the aforementioned parameter.
    :param signature: The gene signature from which these ranked genes originate.
    :return: The leading edge returned as a list of tuple. Each tuple associates a gene part of the leading edge with
        its rank or with its importance (if gene signature supplied).
    """
    rank_threshold = len(rcc)

    def critical_point():
        """ Returns (x,y). """
        x_values = np.arange(1, rank_threshold + 1)
        y_values = rcc - avg2stdrcc
        y_max = y_values.max()
        x_max = int(x_values[y_values == y_max][0])
        return x_max, rcc[x_max - 1]

    def get_genes(rank):
        sorted_idx = np.argsort(ranking)
        sranking = ranking[sorted_idx]
        gene_ids = genes[sorted_idx]
        filtered_idx = sranking < rank
        filtered_gene_ids = gene_ids[filtered_idx]
        if signature is not None:
            return list(zip(filtered_gene_ids, (signature[gid] for gid in filtered_gene_ids)))
        else:
            return list(zip(filtered_gene_ids, sranking[filtered_idx]))

    rank, n_recovered_genes = critical_point()
    return get_genes(rank)


def leading_edge4row(row: pd.Series, avg2stdrcc: np.ndarray, genes: np.ndarray,
                     signature: Optional[Type[GeneSignature]] = None) -> List[Tuple[str,float]]:
    """
    Calculate the leading edge for a row of a dataframe. Should be used with partial function application to make this
    function amenable to the apply idiom common for dataframes.

    :param row: The row of the dataframe to calculate the leading edge for.
    :param avg2stdrcc: The average + 2 standard deviation recovery curve.
    :param genes: The genes corresponding to the ranking available in the supplied row.
    :param signature: The gene signature from which these ranked genes originate.
    :return: The leading edge returned as a list of tuple. Each tuple associates a gene part of the leading edge with
        its rank or with its importance (if gene signature supplied).
    """
    return leading_edge(row['Recovery'].as_matrix(),  avg2stdrcc, row['Ranking'].as_matrix(), genes, signature)