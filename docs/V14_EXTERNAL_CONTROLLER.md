# V14 external controller: USB Mass Storage / UASP vs NVMe (Stage C)

Phase 11 design document (master directive §14 Stage C; [V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P11). It studies
how the V14 device could be presented to a host computer as an **external hardware device** — a box on a cable that
the host enumerates as a disk — instead of a block device created on the host itself
([V14_DEVICE_INTERFACE.md](V14_DEVICE_INTERFACE.md), P8).

**Status: design only. No controller has been assembled, configured, powered or tested. Nothing in this document was
run on hardware; no number in it is a measurement of ours.** Evidence category: none of A–F (a design is not
evidence). The NBD device of P8 (`/dev/nbdN`) and a future ublk device are *host-side software block devices*; neither
is a USB or NVMe device (directive §14: "a ublk device is not a USB/NVMe device").

Every hardware or protocol statement carries a source in §9 (tag `[Sn]`). "Not verified" marks what we could not
confirm from a fetched source. Sources were consulted on 2026-10-18.

## 1. What an external controller has to be

The host must see a standard device class so that no host driver of ours is needed:

```
 host computer ── cable (USB or PCIe) ──► controller board ──► VNX stack on the board ──► media adapter
 (stock OS driver:                        (device-mode USB      (vnxd: VNX-IO, Volume,     (image file on the board's
  usb-storage / uas / nvme)                or PCIe endpoint)     Cache, VNX-RAM, fallback,  storage; DNA simulator;
                                                                  sealing)                  later the dna-lab adapter)
```

The board runs Linux and the unchanged V14 stack. The only new piece is the **device-mode function** that turns the
host's protocol (SCSI over USB, or NVMe over PCIe) into block requests for vnxd.

## 2. Candidates

| | USB MSC, Bulk-Only Transport (BOT) | USB Attached SCSI (UAS/UASP) | NVMe over PCIe (endpoint) |
|---|---|---|---|
| standard | USB-IF *Mass Storage Class Bulk-Only Transport* 1.0 (1999) [S1] | T10 UAS-1 = INCITS 471-2010 (ISO/IEC 14776-251:2014); USB-IF maintains the UASP companion spec [S4][S5] | NVM Express Base 2.4 + NVMe over PCIe Transport 1.4 (ratified 2026-07-31) [S11] |
| command model | one command at a time: 31-byte CBW → data → 13-byte CSW; status 00h passed / 01h failed / 02h phase error [S1] | queued commands on four bulk pipes (command, status, data-in, data-out); out-of-order completion; USB 3 streams, USB 2 uses tagged IUs [S6] | submission/completion queue pairs in host memory; admin + I/O queues [S11][S12] |
| error recovery | phase error → host Reset Recovery (class reset, Clear HALT on both bulk endpoints) [S1] | UAS task management (abort, reset) [S7] | controller reset, command abort (spec) [S11] |
| host driver | Linux `usb-storage`; Windows `usbstor.sys` [S8][S9] | Linux `uas` ("multiple outstanding commands"); Windows `uaspstor.sys` (Windows 8 and later) [S8][S9] | stock `nvme` driver on every major OS (standard reference, not re-fetched) |
| Linux device-side function | `mass_storage` (`usb_f_mass_storage`, configfs), backing file or block device per LUN, up to 8 LUNs [S2][S3] | `tcm` (`usb_f_tcm`) over the LIO SCSI target; the maintainer series of 2022 states the UASP gadget "remained broken for a long time" and was fixed and tested against UASP CV on a DWC_usb3 controller [S7] | `nvmet-pci-epf`: an NVMe target exposed over a PCIe endpoint controller; namespaces backed by a file, block device or passthrough [S12] |
| controller hardware needed | any SoC with a USB device-mode (UDC) port; USB 2.0 high speed is enough [S1][S2] | same; benefits need USB 3 device mode (streams) [S6] | an SoC whose PCIe controller runs in **endpoint** mode with a Linux endpoint driver, plus a crossed (RX/TX swapped) PCIe cable or adapter; DMA optional ("MMIO … poor performance") [S12] |
| maturity in Linux | long-standing | usable since the 2022 fixes; little field record [S7] | new; recent kernels only (the docs build we read is 7.3-rc) [S12] |
| boot from it (host UEFI) | yes, UEFI USB mass storage (see [V14_BOOT_ARCHITECTURE.md](V14_BOOT_ARCHITECTURE.md)) | not verified that common firmware boots via UAS | yes in principle (NVMe boot); `nvmet-pci-epf` "never reports boot partition" support [S12] |

## 3. Decision

**Initial approach: USB Mass Storage, Bulk-Only Transport, served by the Linux `mass_storage` gadget function on a
USB-device-capable Linux board, with each LUN backed by the `/dev/nbdN` device that vnxd itself attaches on that
board (P8 Stage A).** UASP via `f_tcm` is stage C2; NVMe endpoint is stage C3 (research).

Reasons, in the order of the directive's criteria:

1. **Hardware availability.** USB device mode exists on ordinary, purchasable single-board computers (§4); NVMe needs
   a PCIe controller in endpoint mode and a crossed PCIe link, which consumer boards do not ship with a ready cable
   for [S12]; the board reported working with `nvmet-pci-epf` (ROCK 5B) is "not intended for production use"
   in the merge report [S20]. Commercial NVMe controller ASICs with customer-replaceable firmware were not researched.
2. **Protocol complexity.** BOT is one outstanding command, two fixed-size wrappers and one recovery procedure [S1].
   UAS adds queues, tags, streams and task management [S6][S7]; NVMe adds queue pairs in host memory, doorbells,
   MSI-X and PCIe link management [S12].
3. **Controller resources.** The `mass_storage` function uses a kernel thread and a few 16 KiB buffers [S2][S10];
   the VNX stack's own budgets (VNX-RAM, in-flight bytes) dominate.
4. **Cost.** The board is the only new hardware; prices are not quoted here (§4: we quote none we did not see on a
   vendor page).
5. **Firmware.** No firmware of ours: stock kernel function + configfs + the existing `vnxd`. The bring-up is
   configuration, not code.
6. **Use.** Every host OS mounts a BOT disk without drivers [S8][S9]; throughput is bounded by USB 2.0 high speed
   (480 Mbit/s signalling) on most boards — far above what any DNA medium delivers, and the P8 measurement shows
   writes are bound by backend fsyncs, not by the transport ([V14_DEVICE_INTERFACE.md](V14_DEVICE_INTERFACE.md) §13).

What BOT costs us: one command at a time (no queueing across the cable [S1][S6]); errors collapse into a few SCSI
sense codes (§5.4); no TRIM (`UNMAP` is not in the function's command table [S10]). Stage C2 removes the first two.

### 3.1 Stages

| stage | what | class |
|---|---|---|
| C1 | BOT `mass_storage` gadget → `/dev/nbdN` → vnxd, on a USB-2 device-mode board | **buildable now** (stock parts + stock kernel + existing V14 code; not yet assembled) |
| C2 | UASP `tcm` gadget over LIO; backstore either `iblock` on `/dev/nbdN` or a TCMU userspace handler inside vnxd (SCSI commands answered by vnxd directly, precise sense data) [S7][S13]; on a USB 3 device-mode board | **custom engineering** (new vnxd module: TCMU ring handler; UASP gadget maturity to be proven) |
| C3 | NVMe `nvmet-pci-epf` on a PCIe-endpoint SoC, namespace backed by `/dev/nbdN` [S12] | **custom engineering / research** (endpoint hardware + cabling; kernel feature recent) |
| C4 | dedicated controller silicon / FPGA | **out of scope** (no researched part) |

## 4. Candidate boards for C1/C2

Only boards whose USB device-mode support is documented by the vendor or the kernel are listed (details and sources in
[V14_BOOT_ARCHITECTURE.md](V14_BOOT_ARCHITECTURE.md) §2, which also covers their boot security).

| board | device-mode USB | role | source |
|---|---|---|---|
| Raspberry Pi 4 Model B | USB 2.0 controller on the USB-C power connector, device mode by default (`otg_mode=0`); `dwc2` overlay `dr_mode=peripheral` | **C1 reference board** | [S14][S15] |
| Raspberry Pi 5 | listed by Raspberry Pi's `rpi-usb-gadget` (USB-C port); an early `dwc2` probe failure was fixed in later firmware (issue report); USB 2.0 only is our reading, not stated by the vendor | C1 alternative | [S16][S17] |
| Compute Module 4 / 5 | CM5 listed by `rpi-usb-gadget`; "CM4 requires more effort" | C1 for a custom carrier board | [S16] |
| NXP i.MX 8M Plus (e.g. FRDM-IMX8MPLUS) | Type-C "USB 3.0 super-speed host and device controller" (DWC3 dual-role), per the board user manual (search result, not opened) | C2 candidate (USB 3 device mode for UASP streams) | [S18] |
| Radxa ROCK 5B (RK3588) | USB 3 gadget **unconfirmed** (forum reports: device mode at USB 2.0 speed); PCIe 3.0 x4 **endpoint** with `nvmet-pci-epf` reported working (news report of the 6.14 merge, author's figures ≈131 K random-4K IOPS, 2.8 GB/s over null_blk, "not intended for production use") | C3 candidate only | [S19][S20] |
| TI AM64x | device mode high speed only ("USB 3.0 superspeed device mode is not supported", vendor issue tracker, snippet) | not chosen | [S21] |

No Raspberry Pi is documented as a USB 3 *device* (consistent with every source read; no explicit vendor statement
found). That is why C1 is BOT on USB 2.0 and why C2 needs another board.

A further reason for BOT: the open-source UEFI reference (EDK2) `UsbMassStorageDxe` implements only the CBI and
Bulk-Only transports, no UAS [S22]. A device that must also be bootable by host firmware has to offer BOT
(details in [V14_BOOT_ARCHITECTURE.md](V14_BOOT_ARCHITECTURE.md)).

Prices are not given: the founder buys from a local distributor and records the receipt; no price in this repository
is invented.

## 5. Specification of the chosen approach (C1, with C2 differences)

### 5.1 Host protocol

* USB device descriptor: Mass Storage class (08h), SCSI transparent subclass (06h), Bulk-Only protocol (50h) [S1];
  the `mass_storage` function provides them [S2][S3]. Vendor/product ids, manufacturer, product and serial strings
  come from configfs (`idVendor`, `idProduct`, `strings/0x409/*`) [S3]. A product sold outside the lab needs a USB-IF
  vendor id; the lab prototype uses a test id and says so (not verified which test ids are allowed for
  distribution; never ship one).
* Command set (what the host can send): the function's table includes INQUIRY, MODE SENSE/SELECT (6/10), READ and
  WRITE (6/10/12/16), READ CAPACITY (10) and (16), REQUEST SENSE, START STOP UNIT, PREVENT/ALLOW MEDIUM REMOVAL,
  SYNCHRONIZE CACHE, TEST UNIT READY, VERIFY, READ FORMAT CAPACITIES [S10]. No UNMAP: the host cannot TRIM (matches
  the device's TRIM no-op in P8).
* C2: UAS IUs over four bulk pipes; command queue depth negotiated by the host's `uas` driver [S6][S7].

### 5.2 Command handling (mapping onto the V14 stack)

```
host SCSI cmd ─ USB ─► UDC ─► mass_storage kthread ("file-storage") ─ vfs read/write on /dev/nbdN
   ─► kernel NBD driver ─► vnxd nbd server (P8) ─► IoQueue ─► BlockIo (VNX-RAM) ─► ExtentCache
   ─► Volume ─► SealedMedia (if sealed) ─► ResilientMedia ─► MediaAdapter (image | dna-sim | dna-lab)
```

| SCSI | gadget action [S10] | V14 path |
|---|---|---|
| READ(10/16) | read of the backing block device | NBD READ → `IoQueue` read; SHA-256 check before return |
| WRITE(10/16) | write; FUA bit sets `O_SYNC` on the backing file unless `nofua` | NBD WRITE (+FLUSH via the page cache's sync) → Volume transaction |
| SYNCHRONIZE CACHE | `vfs_fsync` of the backing file (whole device; the LBA range is ignored) | NBD FLUSH → queue barrier → backend sync + journal fsync |
| READ CAPACITY (10/16) | backing size / block size, rounded down to whole blocks | the volume's `--size`/`--block-size` (management `capacity`) |
| TEST UNIT READY | medium present? | NOT READY / MEDIUM NOT PRESENT when no backing file is attached (§5.10) |

Rules: `nofua` stays **0** (FUA must reach vnxd); the LUN is not `removable` in normal operation (so the host never
caches across an eject); `ro` is set when vnxd runs `--read-only`. Block size: the function uses the backing block
device's logical block size [S2], i.e. the NBD device's block size, i.e. the volume's 4096.

C2 (TCMU): vnxd maps the TCMU ring [S13] and answers each CDB itself: READ/WRITE/SYNCHRONIZE CACHE straight into
`IoQueue` (no NBD hop, no page cache), and builds sense data from `ErrorCode` (§5.4).

### 5.3 Controller memory

* Gadget: `FSG_BUFLEN` 16 KiB buffers, 2–4 of them (`num_buffers`) [S2][S3][S10].
* Page cache of the board between the gadget and `/dev/nbdN` (C1): bounded by the board's RAM; the host's FLUSH /
  FUA write it through (§5.2). With `--cache write-back` in vnxd, data is acknowledged before reaching the backend
  exactly as on the host-side device (P8 §5): the same rule, the same loss on power cut (§5.6).
* vnxd: the P3 VNX-RAM budget (`--ram`, default 256 MiB) and the P8 in-flight bounds (64 MiB / 128 requests). On a
  1–2 GiB board the budget is set to at most a quarter of RAM (to be measured in P10/Pi acceptance; no number yet).

### 5.4 Queues, errors, timeouts

* C1 queue depth across the cable is 1 (BOT) [S1]; the board's kernel may still coalesce writes into larger NBD
  requests. vnxd's `IoQueue` ordering, deadlines and cancellation apply unchanged.
* Error mapping (C1): a failed read of the backing device becomes sense MEDIUM ERROR / UNRECOVERED READ ERROR, a
  failed write or SYNCHRONIZE CACHE becomes WRITE ERROR, an absent backing file MEDIUM NOT PRESENT, an LBA past the
  end LOGICAL BLOCK ADDRESS OUT OF RANGE [S10]. So every V14 failure (CORRUPTION, BACKEND_UNAVAILABLE, TIMEOUT,
  READ_ONLY …; [V14_CONTRACTS.md](V14_CONTRACTS.md) §5) reaches the host as a SCSI error on that command — **never
  as success and never as other data** — but the class is lost. The class is visible on the management socket
  (`health`, `stats`) and in the audit log.
* C2 mapping (proposed, ours): CORRUPTION → MEDIUM ERROR/UNRECOVERED READ ERROR; READ_ONLY/UNAUTHORIZED → DATA
  PROTECT/WRITE PROTECTED; BACKEND_UNAVAILABLE/BUSY/TIMEOUT → NOT READY (host retries) or ABORTED COMMAND;
  NO_SPACE → not applicable to a fixed-size LUN; OUT_OF_RANGE → ILLEGAL REQUEST. Status values must be SCSI's (e.g.
  CHECK CONDITION = 2) [S13]. To be checked against SPC/SBC before implementation (T10 standards, not fetched).
* Timeouts: the host's SCSI timeout (Linux default per device, not verified per OS) must exceed vnxd's
  `--request-timeout-s` (default 30 s) plus the backend's bounded retries, the same rule as the NBD kernel timeout
  (P8 §5). A DNA-backed LUN whose reads take hours cannot be served synchronously over any of these protocols: reads
  of extents not in the electronic cache return NOT READY / BECOMING READY (C2) or fail (C1) and the host retries
  later (§5.10). TCMU aborts commands after its own timeout (30 s stated) if the handler does not answer [S13].
* Bus reset / BOT Reset Recovery: the gadget re-arms; outstanding work inside vnxd completes and is then discarded by
  the gadget (a write that completed inside vnxd is durable or not according to FLUSH, exactly as for a power cut).

### 5.5 Power-loss handling

* The data rule is P2/P4's: a write is durable only after FLUSH/FUA completed; the journal's map record is the commit
  point; recovery at the next open replays the journal ([V14_FAILURE_AND_RECOVERY_MODEL.md](V14_FAILURE_AND_RECOVERY_MODEL.md) §5).
* The board's own storage (SD card or eMMC) must honour fsync; SD cards are a known weak point (not verified per
  card; the prototype keeps the backend on a USB SSD or NVMe on the board and the journal on eMMC/SSD, never on the
  SD card).
* Hold-up: none in C1 (bus-powered or a wall adapter). A supercapacitor/UPS HAT that lets vnxd flush on power loss is
  an option for C2 (custom; no part selected — not researched in this phase).
* The host sees a disk that vanishes; on reconnect the gadget is only bound to the UDC **after** vnxd has finished
  journal recovery (§5.7), so the host never reads a half-recovered volume.

### 5.6 Firmware startup (board)

1. Board boot chain to Linux ([V14_BOOT_ARCHITECTURE.md](V14_BOOT_ARCHITECTURE.md) §3).
2. systemd starts `vnxd nbd attach` (P8) with the volume and backend on the board; it prints `{"event":"attached",
   "device":"/dev/nbdN"}` only after recovery.
3. A oneshot unit, ordered after that event, creates the configfs gadget (`functions/mass_storage.0/lun.0/file =
   /dev/nbdN`) and writes the UDC name to `UDC` [S3].
4. Health check (`health` = ok, or `degraded` → `ro=1`) before binding; `failed` → the gadget is not bound; the
   status LED/serial console reports why (§5.9).

Shutdown is the reverse: unbind the UDC (host sees removal), `vnxd` SIGTERM (flush + detach, P8).

### 5.7 Media-adapter communications

Unchanged: the media-adapter trait inside vnxd ([V14_MEDIA_ADAPTERS.md](V14_MEDIA_ADAPTERS.md)). On the board:
`image` (electronic file backend on the board's SSD) or `dna-sim` (codec workers on the board's CPU). The
`dna-lab` adapter (§5 of that document) talks to lab equipment over the network, never over the USB cable to the host;
the host never sees DNA operations, only a disk that is slow or not ready.

### 5.8 Device identification, capacity, capability reporting

* USB: vendor/product id + serial string (configfs) [S3]; the serial is the volume's `device_id` (hex) so a host
  can match the disk with the management records.
* SCSI INQUIRY: vendor/product/revision strings come from the gadget (C1, fixed by the function; whether they are
  configurable is not verified for every kernel); C2 sets them in LIO `wwn/` (vendor_id, product_id, revision,
  vpd_unit_serial) [S7].
* Capacity: READ CAPACITY(16) = the volume size [S10]. Capabilities beyond a disk (backend kind, durability,
  sealing, health) are **not** in SCSI: they stay on `vnx.mgmt/1`, reachable from the host over a second USB function
  (e.g. `ncm` network, [S3]) or the board's Ethernet, authenticated as in P4.

### 5.9 Diagnostics

`vnx.mgmt/1` `health`/`stats` (P8 §6) over the network function; the audit log; a serial console; an LED for
`ok/degraded/failed`. SCSI SEND DIAGNOSTIC is in the function's table but is a no-op there [S10].

### 5.10 Physical-media readiness

* Electronic backends: always ready once bound.
* DNA backends: a read of an extent that is not in the electronic cache can take hours to days (synthesis/sequencing
  jobs, [V14_PHYSICAL_DNA_DEVICE.md](V14_PHYSICAL_DNA_DEVICE.md)). A block protocol cannot wait that long. Therefore a
  DNA-backed LUN is exported **only** in one of two modes: (a) *staged*: an archive is first restored to the
  electronic backend (management command, asynchronous), then exported read-only; (b) *write-only archive*: writes
  are acknowledged after the electronic commit and archived to DNA in the background — the LUN says nothing about DNA
  durability; that is reported by management only. Neither mode is implemented; both are designs.

### 5.11 Firmware update and recovery

Board image update (A/B), rollback and recovery are in [V14_BOOT_ARCHITECTURE.md](V14_BOOT_ARCHITECTURE.md) §6.
The volume format is independent of the board image: an update never rewrites the journal; a new vnxd that does not
understand a journal version refuses it (VERSION_MISMATCH) and the old slot is booted.

## 6. Security

Same model as [V14_SECURITY_MODEL.md](V14_SECURITY_MODEL.md): the board is the trust boundary (it holds the sealing
key and the journal); the host is trusted with plaintext of what it reads (it is the user). New threats: physical
theft of the board (key at rest — sealed by the board's secure-boot/OTP key where available, see the boot document),
and a malicious host sending malformed SCSI (handled by the kernel function in C1; by vnxd's TCMU parser in C2, which
must be a strict parser with typed errors like every V14 parser).

## 7. What is not done / open questions

* Nothing assembled; no bandwidth, latency or power figure exists.
* Whether `usb_f_tcm` works on a USB 2.0 `dwc2` controller (Raspberry Pi) is **not verified**; the 2022 fixes were
  tested on DWC_usb3 [S7].
* Host OS timeouts for USB disks (Linux, Windows, macOS) were not researched per OS.
* C3: no PCIe-endpoint board was checked against `nvmet-pci-epf` in this phase (the kernel page names no board [S12]).

## 8. Acceptance tests for a future C1 prototype (not run)

1. Host enumerates the board as a USB disk; `lsusb -v` shows class 08/06/50; capacity equals the volume size.
2. The P8 acceptance sequence through the USB disk: partition, mkfs.ext4, mount, copy, sync, unmount, unplug, replug,
   `e2fsck -f -n`, SHA-256 of every file.
3. Fault drills (`--fault-control`) → host sees I/O errors, never other data; recovery after the probe interval.
4. Power cut of the board after `sync` on the host → all synced files intact after replug.
5. Teardown: UDC unbound, vnxd detached, no `nbd` device left attached on the board.

## 9. Sources

| tag | source | supports |
|---|---|---|
| S1 | USB-IF, *Universal Serial Bus Mass Storage Class Bulk-Only Transport*, Rev 1.0 (library entry dated 1999-09-30): https://www.usb.org/document-library/mass-storage-bulk-only-10 ; text read from the copy at https://www.cs.usfca.edu/~cruse/cs698s10/usbmassbulk_10.pdf | CBW 31 bytes / CSW 13 bytes, signatures 43425355h/53425355h, status 00h/01h/02h, Reset Recovery order, Get Max LUN up to 15 |
| S2 | Linux kernel docs, *Mass Storage Gadget (MSG)*: https://docs.kernel.org/usb/mass-storage.html | per-LUN `file`/`ro`/`removable`/`cdrom`/`nofua`, max 8 LUNs, backing file or block device, block size from the device, two 16K buffers, FUA note |
| S3 | Linux kernel docs, *Linux USB gadget configured through configfs*: https://docs.kernel.org/usb/gadget_configfs.html and *Testing the USB gadget functions*: https://docs.kernel.org/usb/gadget-testing.html | configfs layout, UDC binding, function `mass_storage` (`usb_f_mass_storage`), `num_buffers` 2..4, `forced_eject`, `ncm` |
| S4 | ANSI webstore, INCITS 471-2010 (USB Attached SCSI): https://webstore.ansi.org/standards/incits/incits4712010 | UAS-1 identity |
| S5 | Wikipedia, *USB Attached SCSI*: https://en.wikipedia.org/wiki/USB_Attached_SCSI | ISO/IEC 14776-251:2014; T10 maintains UAS, USB-IF MSC WG maintains UASP (secondary source) |
| S6 | D. Johnson, *What's the Difference Between USB UASP and BOT*, Electronic Design, 2012-03-18: https://www.electronicdesign.com/technologies/embedded/article/21800348/whats-the-difference-between-usb-uasp-and-bot | BOT: no multiple outstanding requests; UASP: four bulk pipes, command queuing, out-of-order completion, streams on USB 3, tagged IUs on USB 2 |
| S7 | T. Nguyen, *[PATCH v2 00/25] usb: gadget: f_tcm: Enhance UASP driver*, LKML 2022-07: https://lkml.iu.edu/hypermail/linux/kernel/2207.2/02424.html | UASP gadget "remained broken for a long time"; fixes incl. task management; tested with UASP CV on DWC_usb3; configfs layout `usb_gadget/.../functions/tcm.0`, `target/usb_gadget/naa.0/tpgt_1`, `wwn/` |
| S8 | Linux `drivers/usb/storage/Kconfig`: https://github.com/torvalds/linux/blob/master/drivers/usb/storage/Kconfig | host `usb-storage`; `uas` "permits higher performance by supporting multiple outstanding commands" |
| S9 | Microsoft, Windows HCK UAS test pages: https://learn.microsoft.com/en-us/previous-versions/windows/hardware/hck/jj124021(v=vs.85) | UAS devices tested against UASP 1.04/SPC-4/SBC-3, Windows 8+; `usbstor.sys`/`uaspstor.sys` naming from secondary sources (archived page) |
| S10 | Linux source `drivers/usb/gadget/function/f_mass_storage.c` and `storage_common.c` (master, fetched 2026-10-18): https://github.com/torvalds/linux/blob/master/drivers/usb/gadget/function/f_mass_storage.c | supported SCSI opcodes (no UNMAP); SYNCHRONIZE CACHE → `vfs_fsync` of the whole file; FUA → `O_SYNC`; sense codes on read/write errors and absent medium; kthread "file-storage"; `FSG_BUFLEN` buffers; SEND DIAGNOSTIC accepted |
| S11 | NVM Express, Base Specification page: https://nvmexpress.org/specification/nvm-express-base-specification/ ; Boot Specification page: https://nvmexpress.org/specification/nvme-boot-specification | Base 2.4 and PCIe transport 1.4 ratified 2026-07-31; NVMe Boot Specification 1.4 |
| S12 | Linux kernel docs, *NVMe PCI Endpoint Function Target*: https://docs.kernel.org/nvme/nvme-pci-endpoint-target.html | endpoint controller + driver required; crossed cable; DMA optional (MMIO slow); queue limits; no boot partition/CMB/PMR; MDTS 512 KB default, 2 MB cap; namespaces on file/block/passthrough |
| S13 | Linux kernel docs, *TCM Userspace Design*: https://docs.kernel.org/target/tcmu-design.html | userspace SCSI backstore via UIO ring; status must be SCSI values; 30 s timeout when the handler does not answer |
| S14 | Raspberry Pi documentation, config.txt boot options (`otg_mode`), source `documentation/asciidoc/computers/config_txt/boot.adoc` at https://github.com/raspberrypi/documentation (rendered: https://www.raspberrypi.com/documentation/computers/config_txt.html) | Pi 4: USB 2.0 controller on the USB-C connector usable as a device, `otg_mode=0` default, Pi 4 only |
| S15 | Raspberry Pi firmware overlays README: https://github.com/raspberrypi/firmware/blob/master/boot/overlays/README | `dwc2` overlay `dr_mode` host/peripheral/otg |
| S16 | Raspberry Pi, `rpi-usb-gadget`: https://github.com/raspberrypi/rpi-usb-gadget | supported: Zero/Zero 2 W, 3A+, 4B, 5, 500, CM0/CM5; "CM4 requires more effort"; uses the USB-C port on Pi 4/5 |
| S17 | Raspberry Pi bookworm-feedback issue 77: https://github.com/raspberrypi/bookworm-feedback/issues/77 (search snippet) | Pi 5 `dwc2` probe failure fixed by later firmware |
| S18 | NXP FRDM-IMX8MPLUS user manual UM12340 (Mouser copy): https://www.mouser.com/catalog/specsheets/NXP%20Semiconductors_08-07-2025_UM12340.pdf (search snippet) | USB 3.0 host and device controller on Type-C |
| S19 | Radxa forum: https://forum.radxa.com/t/rock-5b-usb-otg/16850 and https://forum.radxa.com/t/two-usb-ports-as-gadget-device-mode/24728 (search snippets) | ROCK 5B gadget reported at USB 2.0 speed; USB 3 device mode unconfirmed |
| S20 | Phoronix, *Linux 6.14 PCIe NVMe target EPF*: https://www.phoronix.com/news/Linux-6.14-PCIe-NVMe-Target-EPF | `nvmet-pci-epf` merged for 6.14; tested on ROCK 5B RK3588 endpoint; author's figures; "not intended for production use" |
| S21 | TI issue tracker EXT_EP-12781: https://sir.ext.ti.com/jira/browse/EXT_EP-12781 (search snippet) | AM64x: no USB 3.0 SuperSpeed device mode |
| S22 | EDK2, `MdeModulePkg/Bus/Usb/UsbMassStorageDxe/UsbMassImpl.c` and `UsbMass.h` (tree at c141577, 2026-10-16): https://github.com/tianocore/edk2/tree/master/MdeModulePkg/Bus/Usb/UsbMassStorageDxe | transports CBI0, CBI1, BOT only; no UAS in the upstream tree |
