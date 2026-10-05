# DNA Data Storage Alliance mapping (Sector Zero, Sector One)

Status: **mapping table only. Byte and base layouts: TO BE ALIGNED WITH THE PUBLISHED SPECIFICATION.** Source: spec
§8.1 (`docs/spec/VNX-DNA-SPEC-V6.md`), V6 Phase 7. Founder decision 4 defers the Sector Zero and Sector One layouts to V9
or DDSA membership.

VNX-DNA 6.x produces **no Sector Zero or Sector One bytes or bases**. No export package contains a `sector-zero.fasta` or
`sector-one.fasta` file, and the package roles `ddsa-sector-zero` and `ddsa-sector-one` are reserved in the
`vnx.export-package/1` schema but never written (a test checks this). VNX-DNA holds no DDSA vendor or codec ID
allocation and claims no DDSA conformance.

Public descriptions say that Sector Zero v1.0 is 70 bases (35 identify the vendor, 35 the codec) and that Sector One v1.0
carries archive metadata: content description, file table and sequencer parameters. The specification texts were not
available when this table was written, so nothing below defines their contents.

| DDSA element | VNX source (6.x) | Mapping rule | Layout |
|---|---|---|---|
| Sector Zero vendor ID | none; needs a DDSA allocation | configuration value of the writer, not derived | to be aligned |
| Sector Zero codec ID | VNX codec family: `vnx-frame4` (frame 4, superblocks 1–3) and `vnx-frame6` (frame 6, superblock 3; frame 6 is SPECIFIED, NOT IMPLEMENTED) | one DDSA codec ID per family, not per profile. Profile, geometry and version are self-described by the superblock, which a family reader finds by the spec §3.10 probe | to be aligned |
| Sector One archive metadata | superblock fields (archive ID, container size and SHA-256, geometry, codec identifier, spec and software version); manifest `format`, `format_version`, `required_features`, `counts`, `encoder`, `extensions.vnx`; export-package `archives[]` entry (`archive_id`, `container_sha256`, `container_size`, `codec`, `frame_version`, `superblock_version`, `strand_profile`, `layout`, `outer`) | field-to-field table to be written against the published specification | to be aligned |
| Sector One file table | VNX4 file table (VNX4 §4) | **unencrypted archives only.** For an encrypted archive a writer MUST NOT put names, sizes or hashes in Sector One, because Sector One is readable without a key; only counts permitted by the archive's policy | to be aligned |
| Sector One sequencer parameters | export-package `archives[].strand_nt`, `primers` (null in 6.x), `vendor_constraints`; recommended coverage (not yet recorded) | copied, not recomputed | to be aligned |
| Placement | separate strands added to the pool by the export step (roles `ddsa-sector-zero`, `ddsa-sector-one`) | a VNX reader that does not parse them counts them as orphan reads (they fail the VNX frame check), so adding them does not change a decode. A later reader MAY use Sector One to skip layout detection (§3.10 steps 1–4) and MUST still verify frames and the superblock | to be aligned |

Related: SNIA Swordfish DNA working draft (spec §8.2): the provider operations of `docs/INTEROPERABILITY.md` map onto its
"DNA process" resources; names and schemas to be aligned with the draft; VNX defines no Swordfish resource in 6.x.
JPEG DNA (ISO/IEC 25508-1, spec §8.3): an image payload codec, carried as an ordinary file in a VNX archive; no
conformance claim.
