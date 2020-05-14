"""
LCM: Linear time Closed item set Miner
as described in `http://lig-membres.imag.fr/termier/HLCM/hlcm.pdf`
"""

# Author: Rémi Adon <remi.adon@gmail.com>
# License: BSD 3 clause

from collections import defaultdict
import numpy as np
import pandas as pd
from joblib import Parallel
from joblib import delayed
from roaringbitmap import RoaringBitmap
from sortedcontainers import SortedDict

from ..base import BaseMiner


def _check_min_supp(min_supp):
    if isinstance(min_supp, int):
        if min_supp < 1:
            raise ValueError('Minimum support must be strictly positive')
    elif isinstance(min_supp, float):
        if min_supp < 0 or min_supp > 1:
            raise ValueError('Minimum support must be between 0 and 1')
    else:
        raise TypeError('Mimimum support must be of type int or float')
    return min_supp

class LCM(BaseMiner):
    """
    Linear time Closed item set Miner.

    Parameters
    ----------
    min_supp: int or float, default=0.2
        The minimum support for itemsets to be rendered in the output
        Either an int representing the absolute support, or a float for relative support

        Default to 0.2 (20%)
    n_jobs : int, default=1
        The number of jobs to use for the computation. Each single item is attributed a job
        to discover potential itemsets, considering this item as a root in the search space.
        Processes are preffered over threads.

    References
    ----------
    .. [1]
        Takeaki Uno, Masashi Kiyomi, Hiroki Arimura
        "LCM ver. 2: Efficient mining algorithms for frequent/closed/maximal itemsets", 2004

    .. [2] Alexandre Termier
        "Pattern mining rock: more, faster, better"

    Examples
    --------

    >>> from skmine.itemsets import LCM
    >>> from skmine.datasets.fimi import fetch_chess
    >>> chess = fetch_chess()
    >>> lcm = LCM(min_supp=2000)
    >>> patterns = lcm.fit_discover(chess)
    >>> patterns.head()
        itemset support
    0      (58)    3195
    1  (11, 58)    2128
    2  (15, 58)    2025
    3  (17, 58)    2499
    4  (21, 58)    2224
    >>> patterns[patterns.itemset.map(len) > 3]  # only keeps itemsets longer than 3
    """
    def __init__(self, *, min_supp=0.2, n_jobs=1):
        _check_min_supp(min_supp)
        self.min_supp = min_supp  # provided by user
        self._min_supp = _check_min_supp(self.min_supp)
        self.item_to_tids = None
        self.n_transactions = 0
        self.ctr = 0
        self.n_jobs = n_jobs

    def fit(self, D):
        """fit LCM on the transactional database
        This simply iterates over transactions of D in order to keep
        track of every item and transactions ids related

        Parameters
        ----------
        D : pd.Series or Iterable
            The input transactional database
            Where every entry contain singular items
            Items must be both hashable and comparable

        Returns
        -------
        self:
            a reference to the model itself

        """
        return self._fit(D)

    def _fit(self, D):
        item_to_tids = defaultdict(RoaringBitmap)
        for transaction in D:
            for item in transaction:
                item_to_tids[item].add(self.n_transactions)
            self.n_transactions += 1

        if isinstance(self.min_supp, float):
            # make support absolute if needed
            self._min_supp = self.min_supp * self.n_transactions

        low_supp_items = [k for k, v in item_to_tids.items() if len(v) < self._min_supp]
        for item in low_supp_items:
            del item_to_tids[item]

        self.item_to_tids = SortedDict(item_to_tids)
        return self

    def fit_discover(self, D, return_tids=False):
        """fit LCM on the transactional database, and return the set of
        closed itemsets in this database, with respect to the minium support

        Different from ``fit_transform``, see the `Returns` section below.

        Parameters
        ----------
        D : pd.Series or Iterable
            The input transactional database
            Where every entry contain singular items
            Items must be both hashable and comparable

        return_tids: bool
            Either to return transaction ids along with itemset.
            Default to False, will return supports instead

        Returns
        -------
        pd.DataFrame:
            DataFrame with the following columns
                ==========  =================================
                itemset     a `frozenset` of co-occured items
                support     frequence for this itemset
                ==========  =================================

        Example
        -------
        >>> from skmine.itemsets import LCM
        >>> D = [[1, 2, 3, 4, 5, 6], [2, 3, 5], [2, 5]]
        >>> lcm = LCM(min_supp=2)
        >>> lcm.fit_discover(D)
            itemset  support
        0     (2, 5)        3
        1  (2, 3, 5)        2
        """
        self.fit(D)

        empty_df = pd.DataFrame(columns=['itemset', 'tids'])

        # reverse order of support
        supp_sorted_items = sorted(self.item_to_tids.items(), key=lambda e: len(e[1]), reverse=True)

        dfs = Parallel(n_jobs=self.n_jobs, prefer='processes')(
            delayed(self._explore_item)(item, tids) for item, tids in supp_sorted_items
        )

        dfs.append(empty_df) # make sure we have something to concat
        df = pd.concat(dfs, axis=0, ignore_index=True)
        if not return_tids:
            df.loc[:, 'support'] = df['tids'].map(len).astype(np.uint32)
            df.drop('tids', axis=1, inplace=True)
        return df

    def fit_transform(self, D, sort=True):
        """fit LCM on the transactional database, and return the set of
        closed itemsets in this database, with respect to the minium support.

        This basically calls the ``fit_discover`` method and one-hot-encode
        the resulting patterns. This makes LCM a possible preprocessing step
        in a ``scikit-learn pipeline``.

        Parameters
        ----------
        D : pd.Series or Iterable
            The input transactional database
            Where every entry contain singular items
            Items must be both hashable and comparable

        sort: bool
            if True, columns will be sorted by decreasing order of support

        Returns
        -------
        One-hot-encoded itemsets : pd.DataFrame
            A boolean DataFrame with itemsets as columns, and transactions as rows

        Example
        -------
        >>> from skmine.itemsets import LCM
        >>> D = [[1, 2, 3, 4, 5, 6], [2, 3, 5], [2, 5]]
        >>> lcm = LCM(min_supp=2)
        >>> lcm.fit_transform(D, sort=True)
           (2, 5)  (2, 3, 5)
        0       1          1
        1       1          1
        2       1          0
        """
        df = self.fit_discover(D, return_tids=True)
        if sort:
            index = df.tids.map(len).sort_values(ascending=False).index
            df = df.reindex(index)
        shape = (self.n_transactions, len(df))
        mat = np.zeros(shape, dtype=np.uint32)
        for idx, tids in enumerate(df['tids']):
            mat[tids, idx] = 1.0
        return pd.DataFrame(mat, columns=df['itemset'].values)


    def _explore_item(self, item, tids):
        it = self._inner(frozenset(), tids, item)
        df = pd.DataFrame(data=it, columns=['itemset', 'tids'])
        if not df.empty:
            print('LCM found {} new itemsets from item : {}'.format(len(df), item))
        return df

    def _inner(self, p, tids, limit):
        # project and reduce DB w.r.t P
        cp = (
            item for item, ids in reversed(self.item_to_tids.items())
            if tids.issubset(ids) if item not in p
        )

        max_k = next(cp, None)  # items are in reverse order, so the first consumed is the max

        if max_k and max_k == limit:
            p_prime = p | set(cp) | {max_k}  # max_k has been consumed when calling next()
            yield p_prime, tids

            candidates = self.item_to_tids.keys() - p_prime
            candidates = candidates[:candidates.bisect_left(limit)]
            for new_limit in candidates:
                ids = self.item_to_tids[new_limit]
                if tids.intersection_len(ids) >= self._min_supp:
                    new_limit_tids = tids.intersection(ids)
                    yield from self._inner(p_prime, new_limit_tids, new_limit)