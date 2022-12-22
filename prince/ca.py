"""Correspondence Analysis (CA)"""
import functools
import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.utils import check_array

from prince import plot
from prince import utils
from prince import svd


def select_active_columns(method):
    @functools.wraps(method)
    def _impl(self, X=None, *method_args, **method_kwargs):
        if hasattr(self, "active_cols_") and isinstance(X, pd.DataFrame):
            return method(self, X[self.active_cols_], *method_args, **method_kwargs)
        return method(self, X, *method_args, **method_kwargs)

    return _impl


def select_active_rows(method):
    @functools.wraps(method)
    def _impl(self, X=None, *method_args, **method_kwargs):
        if hasattr(self, "active_rows_") and isinstance(X, pd.DataFrame):
            return method(self, X.loc[self.active_rows_], *method_args, **method_kwargs)
        return method(self, X, *method_args, **method_kwargs)

    return _impl


class CA(utils.EigenvaluesMixin):
    def __init__(
        self,
        n_components=2,
        n_iter=10,
        copy=True,
        check_input=True,
        random_state=None,
        engine="sklearn",
    ):
        self.n_components = n_components
        self.n_iter = n_iter
        self.copy = copy
        self.check_input = check_input
        self.random_state = random_state
        self.engine = engine

    def fit(self, X, y=None):

        # Check input
        if self.check_input:
            check_array(X)

        # Check all values are positive
        if (X < 0).any().any():
            raise ValueError("All values in X should be positive")

        _, row_names, _, col_names = utils.make_labels_and_names(X)

        if isinstance(X, pd.DataFrame):
            X = X.to_numpy()

        if self.copy:
            X = np.copy(X)

        # Compute the correspondence matrix which contains the relative frequencies
        X = X.astype(float) / np.sum(X)

        # Compute row and column masses
        self.row_masses_ = pd.Series(X.sum(axis=1), index=row_names)
        self.col_masses_ = pd.Series(X.sum(axis=0), index=col_names)

        self.active_rows_ = self.row_masses_.index.unique()
        self.active_cols_ = self.col_masses_.index.unique()

        # Compute standardised residuals
        r = self.row_masses_.to_numpy()
        c = self.col_masses_.to_numpy()
        S = sparse.diags(r**-0.5) @ (X - np.outer(r, c)) @ sparse.diags(c**-0.5)

        # Compute SVD on the standardised residuals
        self.svd_ = svd.compute_svd(
            X=S,
            n_components=min(self.n_components, min(X.shape) - 1),
            n_iter=self.n_iter,
            random_state=self.random_state,
            engine=self.engine,
        )

        # Compute total inertia
        self.total_inertia_ = np.einsum("ij,ji->", S, S.T)

        self.row_contributions_ = pd.DataFrame(
            np.diag(self.row_masses_)
            @ (
                # Same as row_coordinates(X)
                (np.diag(self.row_masses_**-0.5) @ self.svd_.U @ np.diag(self.svd_.s))
                ** 2
            )
            / self.eigenvalues_,
            index=self.row_masses_.index,
        )

        self.column_contributions_ = pd.DataFrame(
            np.diag(self.col_masses_)
            @ (
                # Same as col_coordinates(X)
                (
                    np.diag(self.col_masses_**-0.5)
                    @ self.svd_.V.T
                    @ np.diag(self.svd_.s)
                )
                ** 2
            )
            / self.eigenvalues_,
            index=self.col_masses_.index,
        )

        return self

    @property
    @utils.check_is_fitted
    def eigenvalues_(self):
        """Returns the eigenvalues associated with each principal component."""
        return np.square(self.svd_.s)

    @select_active_columns
    def row_coordinates(self, X):
        """The row principal coordinates."""

        _, row_names, _, _ = utils.make_labels_and_names(X)

        if isinstance(X, pd.DataFrame):
            try:
                X = X.sparse.to_coo().astype(float)
            except AttributeError:
                X = X.to_numpy()

        if self.copy:
            X = X.copy()

        # Normalise the rows so that they sum up to 1
        if isinstance(X, np.ndarray):
            X = X / X.sum(axis=1)[:, None]
        else:
            X = X / X.sum(axis=1)

        return pd.DataFrame(
            data=X @ sparse.diags(self.col_masses_.to_numpy() ** -0.5) @ self.svd_.V.T,
            index=row_names,
        )

    @select_active_columns
    def row_cos2(self, X):
        """Return the cos2 for each row against the dimensions.

        The cos2 value gives an indicator of the accuracy of the row projection on the dimension.

        Values above 0.5 usually means that the row is relatively accurately well projected onto that dimension. Its often
        used to identify which factor/dimension is important for a given element as the cos2 can be interpreted as the proportion
        of the variance of the element attributed to a particular factor.

        """
        F = self.row_coordinates(X)

        # Active
        X_act = X.loc[self.active_rows_]
        X_act = X_act / X_act.sum().sum()
        marge_col = X_act.sum(axis=0)
        Tc = X_act.div(X_act.sum(axis=1), axis=0).div(marge_col, axis=1) - 1
        dist2_row = (Tc**2).mul(marge_col, axis=1).sum(axis=1)

        # Supplementary
        X_sup = X.loc[X.index.difference(self.active_rows_)]
        X_sup = X_sup.div(X_sup.sum(axis=1), axis=0)
        dist2_row_sup = ((X_sup - marge_col) ** 2).div(marge_col, axis=1).sum(axis=1)

        dist2_row = pd.concat((dist2_row, dist2_row_sup))

        # Can't use pandas.div method because it doesn't support duplicate indices
        return F**2 / dist2_row.to_numpy()[:, None]

    @select_active_rows
    def column_coordinates(self, X):
        """The column principal coordinates."""

        _, _, _, col_names = utils.make_labels_and_names(X)

        if isinstance(X, pd.DataFrame):
            is_sparse = X.dtypes.apply(pd.api.types.is_sparse).all()
            if is_sparse:
                X = X.sparse.to_coo()
            else:
                X = X.to_numpy()

        if self.copy:
            X = X.copy()

        # Transpose and make sure the rows sum up to 1
        if isinstance(X, np.ndarray):
            X = X.T / X.T.sum(axis=1)[:, None]
        else:
            X = X.T / X.T.sum(axis=1)

        return pd.DataFrame(
            data=X @ sparse.diags(self.row_masses_.to_numpy() ** -0.5) @ self.svd_.U,
            index=col_names,
        )

    @select_active_rows
    def column_cos2(self, X):
        """Return the cos2 for each column against the dimensions.

        The cos2 value gives an indicator of the accuracy of the column projection on the dimension.

        Values above 0.5 usually means that the column is relatively accurately well projected onto that dimension. Its often
        used to identify which factor/dimension is important for a given element as the cos2 can be interpreted as the proportion
        of the variance of the element attributed to a particular factor.
        """
        G = self.column_coordinates(X)

        # Active
        X_act = X[self.active_cols_]
        X_act = X_act / X_act.sum().sum()
        marge_row = X_act.sum(axis=1)
        Tc = X_act.div(marge_row, axis=0).div(X_act.sum(axis=0), axis=1) - 1
        dist2_col = (Tc**2).mul(marge_row, axis=0).sum(axis=0)

        # Supplementary
        X_sup = X[X.columns.difference(self.active_cols_)]
        X_sup = X_sup.div(X_sup.sum(axis=0), axis=1)
        dist2_col_sup = (
            ((X_sup.sub(marge_row, axis=0)) ** 2).div(marge_row, axis=0).sum(axis=0)
        )

        dist2_col = pd.concat((dist2_col, dist2_col_sup))
        return (G**2).div(dist2_col, axis=0)

    def plot_coordinates(
        self,
        X,
        ax=None,
        figsize=(6, 6),
        x_component=0,
        y_component=1,
        show_row_labels=True,
        show_col_labels=True,
        **kwargs,
    ):
        """Plot the principal coordinates."""

        if ax is None:
            fig, ax = plt.subplots(figsize=figsize)

        # Add style
        ax = plot.stylize_axis(ax)

        # Get labels and names
        row_label, row_names, col_label, col_names = utils.make_labels_and_names(X)

        # Plot row principal coordinates
        row_coords = self.row_coordinates(X)
        ax.scatter(
            row_coords[x_component], row_coords[y_component], **kwargs, label=row_label
        )

        # Plot column principal coordinates
        col_coords = self.column_coordinates(X)
        ax.scatter(
            col_coords[x_component], col_coords[y_component], **kwargs, label=col_label
        )

        # Add row labels
        if show_row_labels:
            x = row_coords[x_component]
            y = row_coords[y_component]
            for xi, yi, label in zip(x, y, row_names):
                ax.annotate(label, (xi, yi))

        # Add column labels
        if show_col_labels:
            x = col_coords[x_component]
            y = col_coords[y_component]
            for xi, yi, label in zip(x, y, col_names):
                ax.annotate(label, (xi, yi))

        # Legend
        ax.legend()

        # Text
        ax.set_title("Principal coordinates")
        ei = self.explained_inertia_
        ax.set_xlabel(
            "Component {} ({:.2f}% inertia)".format(x_component, 100 * ei[x_component])
        )
        ax.set_ylabel(
            "Component {} ({:.2f}% inertia)".format(y_component, 100 * ei[y_component])
        )

        return ax
