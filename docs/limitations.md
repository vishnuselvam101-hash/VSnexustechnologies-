# Limitations
The archive is JSON and intended for modest R&D data, not high-volume archival throughput. Current Reed–Solomon blocks are limited to 255 symbols and do not correct insertions/deletions. The UI is the local FastAPI OpenAPI interface rather than a full browser dashboard. There is no wet-lab validation, molecular addressing, capacity, stability, cost, or commercial-readiness claim.

The API is deliberately intended for loopback-only trusted research use. It is not an authenticated multi-user service. Deploy it behind appropriate authentication and path policy before any network exposure.
