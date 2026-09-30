# VNX-DNA overview PDF

Builds `docs/VNX-DNA-v2-overview.pdf` from the measured results. Diagrams are drawn; every chart and measured number
is read from `research/results/v2/*.json`.

```bash
python3 -m venv /tmp/pdfenv && /tmp/pdfenv/bin/pip install reportlab matplotlib numpy
/tmp/pdfenv/bin/python research/v2/overview_pdf/figures.py research/results/v2 /tmp/fig
echo '{"version": "2.0.0", "commit": "<release commit>", "tag": "v2.0.0", "tests": 406, "date": "30 September 2026", "repo": "vishnuselvam101-hash/VSnexustechnologies-"}' > /tmp/meta.json
/tmp/pdfenv/bin/python research/v2/overview_pdf/build_pdf.py /tmp/fig docs/VNX-DNA-v2-overview.pdf /tmp/meta.json
```

Tested with reportlab 5.0.1, matplotlib 3.11.2 and numpy 2.5.3.
