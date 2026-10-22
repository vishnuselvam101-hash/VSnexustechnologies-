# Cost planner (AP-8)

`vnx plan` answers one question before an order: **how many strands and bases does this file need, and what would each
vendor charge for them?**

It measures the size of one file and does arithmetic on it. It does not encode, synthesise, store or sequence anything:
no DNA is synthesised, stored or sequenced by this command. The counts are the software's own encoder counts, and the
prices are numbers **you** supply — the software ships no prices of its own.

---

## The command

    vnx plan FILE [--profile NAME] [--prices PRICES.json]

| option | meaning |
|---|---|
| `FILE` | the file to plan. Only its size is measured; the file is not read and not encoded. |
| `--profile NAME` | the layout profile to plan for. Default `v4-balanced`. |
| `--prices PRICES.json` | a vendor price file (see below). Without it the plan has no costs. |

The plan is printed as JSON on stdout and the command exits 0. A file that cannot be measured exits 3; an unknown
profile or a bad price file exits 7. The output is the usual `vnx.result/1` envelope with the kind `plan`: the plan
document is its `result` field, and — like every other command — the plan's fields are mirrored at the top level of
the CLI output.

Example (the envelope, shortened: `provenance` and `software` are elided):

    $ vnx plan data.bin --profile v4-balanced --prices docs/examples/vendor-prices.example.json
    {
      "schema": "vnx.result/1",
      "kind": "plan",
      "status": "SUCCESS",
      "inputs": [
        {"role": "input", "path": "data.bin", "bytes": 4096, "sha256": null},
        {"role": "prices", "path": "docs/examples/vendor-prices.example.json", "bytes": 474, "sha256": null}
      ],
      "result": {
        "profile": "v4-balanced",
        "input_bytes": 4096,
        "groups": 2,
        "strands": 147,
        "bases": 46011,
        "strand_nt": 313,
        "data_strands": 103,
        "parity_strands": 32,
        "column_parity_strands": 0,
        "metadata_strands": 12,
        "costs": [
          {
            "vendor": "example-vendor-a",
            "currency": "USD",
            "total": "633.61",
            "fits": true,
            "reason": null
          },
          {
            "vendor": "example-vendor-b",
            "currency": "EUR",
            "total": "0",
            "fits": false,
            "reason": "min_strands 200 not met (have 147)"
          }
        ],
        "evidence": "THEORETICAL (encoder count)"
      },
      "error": null
    }

(The numbers above are the ones in `docs/examples/bad-plan.example.json`; `docs/examples/plan.example.json` is the same
command for a 1048576-byte file. Both files hold the plan document alone — the `result` object of the envelope.)

## The plan document

| field | meaning |
|---|---|
| `profile` | the layout profile the plan was made for. |
| `input_bytes` | the size of the measured file. |
| `groups` | the number of data groups (one outer-code block each). |
| `strands` | the total number of strands to synthesise. |
| `bases` | the total number of bases to synthesise: `strands × strand_nt`. |
| `strand_nt` | the length of one strand in nucleotides. |
| `data_strands` | strands carrying payload symbols. |
| `parity_strands` | strands carrying outer-code parity symbols. |
| `column_parity_strands` | strands carrying column parity (V6 layouts only; 0 otherwise). |
| `metadata_strands` | superblock strands (the strand pool's own description). |
| `costs` | one entry per vendor of the price file; empty without `--prices`. |
| `evidence` | `THEORETICAL (encoder count)`: the counts are the encoder's own counts, not a density estimate. |

The counts are **measured software counts**: they are what the encoder's own primitives produce for that file size (the
outer codec's transmitted-symbol count per group and the superblock's strand count), computed in
`src/vnxdna/pipeline/plan.py`. They are not derived from a rounded density formula, and the same plan for the same
file and profile is reproducible.

The profile names are the layout profiles of `src/vnxdna/dnaenc/layout.py` (`v4-balanced` — the default — `v4-dense`,
`v4-indel`, `v4-archival`, `v6-high-dropout`, `v7-lowcov`). The strand length of each profile is reported in
`strand_nt`.

## The cost formula

For every vendor of the price file, one entry:

    total = per_order + per_strand × strands + per_base × bases

* a term is added only when the price file gives that field;
* the arithmetic is exact decimal arithmetic (no floating point); `total` is that decimal as a string;
* `strands` and `bases` are the counts of the plan.

Before the total is computed, the vendor's limits are checked, in this order:

1. **`max_strand_nt`** — if the plan's `strand_nt` is longer than the vendor's maximum, the entry is `fits: false` and
   the `reason` names both numbers.
2. **`min_strands`** — if the plan has fewer strands than the vendor's minimum order, the entry is `fits: false` and
   the `reason` names both numbers.
3. otherwise the entry is `fits: true`, `reason: null` and `total` holds the cost.

A `fits: false` entry is a refusal, not an error: the plan is still printed and the command still exits 0.

## The price file

A JSON object with the format string `vnx.vendor-prices/1` (the constant `PRICE_FORMAT` in
`src/vnxdna/pipeline/plan.py`) and a `vendors` array:

```json
{
  "format": "vnx.vendor-prices/1",
  "vendors": [
    {
      "vendor": "example-vendor-a",
      "currency": "USD",
      "per_base": "0.01",
      "per_strand": "0.50",
      "per_order": "100.00",
      "max_strand_nt": 400,
      "min_strands": 1
    }
  ]
}
```

| field | required | meaning |
|---|---|---|
| `vendor` | yes | the name shown in the cost list. |
| `currency` | yes | a 3-letter uppercase ISO 4217 code (`USD`, `EUR`, …). |
| `per_base` | no | price per synthesised base, as a decimal **string**. |
| `per_strand` | no | price per oligo, as a decimal string. |
| `per_order` | no | fixed fee per order, as a decimal string. Defaults to 0. |
| `max_strand_nt` | no | refuse a plan whose `strand_nt` is longer than this. |
| `min_strands` | no | refuse a plan with fewer strands than this. |

Prices are written as strings on purpose: the cost is computed in exact decimal arithmetic, and a JSON number would
already have lost precision. No other top-level or vendor key is accepted; a file with one is refused with exit 7 and
a message naming the offending key.

**The prices are yours.** `docs/examples/vendor-prices.example.json` is a template with obviously fake example prices
("example only, not real prices") — it is not a price list, and nothing in the software knows what any vendor charges.
Replace it with the numbers from your own quote.

## Examples in this repository

| file | what it shows |
|---|---|
| `docs/examples/vendor-prices.example.json` | the price file format with two example vendors and fake prices. |
| `docs/examples/plan.example.json` | a plan for a 1048576-byte file, `v4-balanced` layout, the example prices: 32787 strands, 10262331 bases, both example vendors fit. |
| `docs/examples/bad-plan.example.json` | the same prices and layout for a 4096-byte file: 147 strands, 46011 bases, and `example-vendor-b` does not fit because the order is below its 200-strand minimum. |

The two plan examples carry three extra keys — `note`, `input` and `command` — that document what they are; the command
itself prints the plan fields only.

## What this is not

* The prices are the operator's, and are as good as the quote they come from.
* The counts describe the **software's** encoding of a file size. A real order adds what the vendor adds (coupon
  synthesis, adapters, splitting a pool across several plates).
* Nothing here was synthesised, stored or sequenced: the planner only counts and multiplies.
