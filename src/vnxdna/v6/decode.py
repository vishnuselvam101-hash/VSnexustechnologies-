"""Pass-2 support for the V6 outer code: rows that fail row-wise are recovered through their stripe's column code.

The V4 pass 2 decodes every row (data and column-parity groups) independently, as before. A row with fewer than
k verified symbols is handed to :class:`StripeRecovery` with the verified symbols it has. After the row pass, each
stripe containing such a row is assembled in the full-position view (decoded rows re-encoded, partial rows with
their verified symbols, padding as known zeros) and decoded iteratively (vnxdna.v6.outer.decode_stripe). Recovered
data rows are written to the container under reconstruction; rows still unknown are reported as failed, exactly as
V4 reports a failed group. Nothing here bypasses the final container SHA-256 + structural validation.

The recovery also accounts for strand loss explicitly: expected versus received symbols per row, rows with any loss,
the largest loss in one row and rows lost entirely (no verified symbol).
"""
from __future__ import annotations

import os

import numpy as np

from .outer import Geometry, StripeStats, decode_stripe, row_codewords, stripe_padding


class _ParityRows:
    """Decoded column-parity rows (each K × P bytes). In memory by default; with ``path`` they are kept in a scratch
    file (row g at offset (g − G)·K·P), so pass-2 memory does not grow with the archive."""

    def __init__(self, geo: Geometry, path: str | os.PathLike | None = None):
        self.geo = geo
        self.mem: dict[int, np.ndarray] | None = None if path else {}
        self.present: set[int] = set()
        self.fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600) if path else None

    def __contains__(self, g: int) -> bool:
        return g in self.present

    def __setitem__(self, g: int, data: np.ndarray) -> None:
        data = np.ascontiguousarray(data, dtype=np.uint8).reshape(self.geo.K, self.geo.P)
        self.present.add(g)
        if self.fd is None:
            self.mem[g] = data
        else:
            os.pwrite(self.fd, data.tobytes(), (g - self.geo.G) * self.geo.K * self.geo.P)

    def __getitem__(self, g: int) -> np.ndarray:
        if g not in self.present:
            raise KeyError(g)
        if self.fd is None:
            return self.mem[g]
        n = self.geo.K * self.geo.P
        return np.frombuffer(os.pread(self.fd, n, (g - self.geo.G) * n), dtype=np.uint8).reshape(self.geo.K, self.geo.P)

    def __len__(self) -> int:
        return len(self.present)

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class StripeRecovery:
    def __init__(self, geo: Geometry, scratch: str | os.PathLike | None = None):
        """``scratch``: optional file for decoded column-parity rows (bounded memory for large archives)."""
        self.geo = geo
        self.pending: dict[int, dict[int, np.ndarray]] = {}     # row → verified symbols (failed row-wise)
        self.errors: dict[int, str] = {}
        self.parity_rows = _ParityRows(geo, scratch)            # decoded column-parity rows: data (K, P)
        self.stats = StripeStats()
        self.recovered: list[int] = []
        self.unrecovered: list[int] = []
        self.stripes_attempted = 0
        self.stripes_skipped = 0
        self.expected = 0
        self.received = 0
        self.rows_with_loss = 0
        self.rows_lost_entirely = 0
        self.max_row_loss = 0
        self._busy = False

    # ---------------------------------------------------------------- row pass
    def _account(self, g: int, syms: dict) -> None:
        n = self.geo.symbols_of(g)
        got = sum(1 for s in syms if 0 <= s < n)
        self.expected += n
        self.received += got
        if got < n:
            self.rows_with_loss += 1
            self.max_row_loss = max(self.max_row_loss, n - got)
        if got == 0:
            self.rows_lost_entirely += 1

    def row_decoded(self, g: int, data: np.ndarray, syms: dict) -> None:
        self._account(g, syms)
        if g >= self.geo.G:
            self.parity_rows[g] = data

    def row_failed(self, g: int, syms: dict, error: str) -> None:
        self._account(g, syms)
        n = self.geo.symbols_of(g)
        self.pending[g] = {s: v for s, v in syms.items() if 0 <= s < n}
        self.errors[g] = error

    # ---------------------------------------------------------------- column pass
    def _data_row(self, fd: int, g: int) -> np.ndarray:
        geo = self.geo
        start = g * geo.K * geo.P
        raw = os.pread(fd, geo.k_of(g) * geo.P, start)
        out = np.zeros((geo.K, geo.P), dtype=np.uint8)
        out.reshape(-1)[: len(raw)] = np.frombuffer(raw, dtype=np.uint8)
        return out

    def finish(self, fd: int, run, done: set, failed: dict, admit=None) -> int:
        """Column pass over every stripe with a pending row. Returns the number of data rows written.

        ``admit(stripe) -> bool`` (the recovery planner's outer budget) may refuse a stripe; its failed data rows then
        stay failed, exactly as if the column code could not recover them."""
        if self._busy or not self.pending:
            return 0
        self._busy = True
        geo = self.geo
        K, M, D, Mc, P = geo.K, geo.M, geo.D, geo.Mc, geo.P
        written = 0
        try:
            for s in sorted({geo.stripe_of(g) for g in self.pending}):
                data_g, par_g = geo.stripe_rows(s)
                rows = data_g + par_g
                todo = set(rows) - done
                if todo:                                   # random access: the other rows of the stripe are needed now
                    run(todo, done)
                mine = [g for g in rows if g in self.pending]
                if not mine:
                    continue
                if admit is not None and not admit(s):
                    for g in mine:
                        if g < geo.G:
                            failed[g] = f"{self.errors[g]}; stripe {s} not attempted (recovery budget max_outer_stripes)"
                            self.unrecovered.append(g)
                        del self.pending[g]
                    self.stripes_skipped += 1
                    continue
                self.stripes_attempted += 1
                index = {g: r for r, g in enumerate(data_g)}
                index.update({g: D + i for i, g in enumerate(par_g)})
                pad = stripe_padding(geo, s)
                cw = np.zeros((D + Mc, K + M, P), dtype=np.uint8)
                known = pad.copy()
                full_rows, full_data = [], []
                for g in rows:
                    r = index[g]
                    if g in self.pending:
                        for t, v in self.pending[g].items():
                            pos = geo.position(g, t)
                            cw[r, pos] = v
                            known[r, pos] = True
                    elif g >= geo.G:
                        if g in self.parity_rows:
                            full_rows.append(r)
                            full_data.append(self.parity_rows[g])
                    elif g in done and g not in failed:
                        full_rows.append(r)
                        full_data.append(self._data_row(fd, g))
                if full_rows:
                    cw[full_rows] = row_codewords(np.stack(full_data), K, M)
                    known[full_rows] = True
                if Mc:
                    cw, known = decode_stripe(cw, known, K, M, D, Mc, self.stats)
                for g in mine:
                    r = index[g]
                    if known[r, :K].all():
                        del self.pending[g]
                        self.recovered.append(g)
                        if g >= geo.G:
                            self.parity_rows[g] = cw[r, :K].copy()
                            continue
                        raw = cw[r, : geo.k_of(g)].reshape(-1).tobytes()
                        start = g * K * P
                        raw = raw[: max(0, min(len(raw), geo.container_size - start))]
                        os.pwrite(fd, raw, start)
                        written += 1
                    elif g < geo.G:
                        failed[g] = (f"{self.errors[g]}; column code (stripe {s}, {D} + {Mc} rows) could not recover "
                                     "it either")
                        self.unrecovered.append(g)
        finally:
            self._busy = False
        return written

    def report(self) -> dict:
        geo = self.geo
        rec_data = [g for g in self.recovered if g < geo.G]
        return {
            "geometry": geo.to_dict(),
            "symbols_expected": self.expected, "symbols_received": self.received,
            "symbols_missing": self.expected - self.received,
            "rows_with_loss": self.rows_with_loss, "rows_lost_entirely": self.rows_lost_entirely,
            "max_symbols_lost_in_one_row": self.max_row_loss,
            "rows_failed_row_wise": len(self.recovered) + len(self.unrecovered) + len(self.pending),
            "rows_recovered_by_columns": len(self.recovered), "data_rows_recovered_by_columns": len(rec_data),
            "data_rows_unrecovered": len(self.unrecovered),
            "stripes_attempted": self.stripes_attempted, "stripes_skipped_by_budget": self.stripes_skipped,
            "row_decodes": self.stats.row_decodes,
            "column_decodes": self.stats.column_decodes,
        }
