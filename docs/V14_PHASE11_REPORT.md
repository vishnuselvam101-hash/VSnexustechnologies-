# V14 Phase 11 report: hardware and boot engineering (design)

Date 2026-10-18. Branch `work/v14-p11` from `build/v14` a32cbf5. Scope: [V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P11
(master directive §14 Stage C, §17, §17.1, §17.2, §18). Documentation and research only: no code, no build, no test
run, no hardware.

Evidence categories: none. A design is not evidence; nothing in this phase is A–F. **No controller, board or DNA
device exists; no DNA was synthesised, stored, retrieved or sequenced; no host has booted from a VNX device.**

## Delivered

* [V14_EXTERNAL_CONTROLLER.md](V14_EXTERNAL_CONTROLLER.md): USB MSC (BOT) vs UASP vs NVMe study with sources, the
  decision, and the Stage C specification for the chosen approach (host protocol, command handling, controller
  memory, queues/errors/timeouts, power loss, firmware startup, media-adapter communication, identification,
  capacity/capabilities, diagnostics, physical-media readiness, update/recovery), mapped onto the existing stack:
  `mass_storage` gadget → `/dev/nbdN` → `vnxd` (P8) → IoQueue → BlockIo/VNX-RAM → ExtentCache → Volume →
  SealedMedia → ResilientMedia → media adapter. 22 sources.
* [V14_BOOT_ARCHITECTURE.md](V14_BOOT_ARCHITECTURE.md): controller boot chain (Raspberry Pi 4/5, CM4/CM5), signed boot
  and its irreversibility, measured-boot limits, first-boot provisioning and key handling consistent with
  V14_SECURITY_MODEL, A/B update with `tryboot` and rollback, recovery, host boot through UEFI Block I/O (EDK2 USB
  BOT path), boot-region layout, manifest verification, VM test architecture (QEMU + OVMF + `vnxd nbd serve`),
  failure modes. 20 sources.
* [V14_PHYSICAL_DNA_DEVICE.md](V14_PHYSICAL_DNA_DEVICE.md): hybrid device design (electronic controller + orchestrated
  lab tier) from researched parts and processes, every component classified, mapping onto `vnx.dna-lab/1`, the
  electronic metadata region (§17.1), constraints, and a proposed path to a first physical test. 25 sources.

## Decision (external controller)

**Initial approach: USB Mass Storage, Bulk-Only Transport, via the Linux `mass_storage` gadget on a Raspberry Pi 4
class board, each LUN backed by the board-local `/dev/nbdN` that vnxd attaches (stage C1).** Reasons:
1. hardware: USB device mode is documented on purchasable boards (Pi 4 USB-C, Pi 5, CM5); NVMe needs a PCIe endpoint
   SoC and a crossed link, and the one reported working target (`nvmet-pci-epf`, Linux 6.14, ROCK 5B) is "not intended
   for production use";
2. protocol: BOT is one command at a time with fixed wrappers and one recovery procedure; UAS and NVMe add queues,
   tags/streams, task management or PCIe queue machinery;
3. firmware: none of ours — a stock kernel function, configfs and the existing `vnxd`;
4. use: every host OS mounts BOT without drivers, and upstream EDK2 boots USB mass storage over BOT/CBI only (no UAS),
   so only BOT keeps the boot path open;
5. throughput: P8 writes are bound by backend fsyncs, not by the transport; USB 2.0 is not the bottleneck for an
   electronic backend and is far above any DNA rate.
Costs accepted: queue depth 1 across the cable, SCSI sense codes coarser than the V14 error classes (classes stay on the
management socket), no TRIM. C2 = UASP (`f_tcm` + TCMU handler in vnxd) on a USB 3 device-mode board; C3 = NVMe
endpoint (research).

## Classification counts

| document | buildable-now | custom | lab-only | other |
|---|---|---|---|---|
| external controller (stages) | 1 (C1) | 2 (C2, C3) | 0 | 1 out of scope (C4 dedicated silicon) |
| boot architecture (§7.7) | 5 | 3 | 0 | — |
| physical DNA device (§3, 29 components) | 8 | 9 | 12 | — |

## Acceptance (P11 criteria of V14_MASTER_PLAN)

| criterion | result | evidence |
|---|---|---|
| V14_BOOT_ARCHITECTURE written | PASS | docs/V14_BOOT_ARCHITECTURE.md |
| external-controller study (USB MSC/UASP vs NVMe) with a decision and the §14 Stage C specification | PASS | docs/V14_EXTERNAL_CONTROLLER.md §2–§5 |
| physical DNA device design with researched parts | PASS | docs/V14_PHYSICAL_DNA_DEVICE.md §3, §8 |
| every hardware statement sourced | PASS, with labels: every statement carries a source tag; sources only seen as search snippets, or not opened (UEFI spec pages returned 403), are marked as such; claims we could not confirm are written as "not verified" | §9 / §8 source tables |
| buildable-now / custom / lab-only classification | PASS | counts above |
| no claim that the device exists | PASS | status lines at the top of each document; §7 of the device document lists what must happen before "built" may be used |
| no invented part numbers, vendor commands, prices or capabilities | PASS | prices only where the vendor page showed one (MinION Mk1D, Opentrons Flex/OT-2); no vendor API is modelled; no BOM total |

Phase 11: **PASS (design)**. It does not advance directive §22 criteria that need hardware (15: Pi acceptance; 17
lists the remaining physical work, which these documents provide).

## Not done / open questions for the founder

1. **Secure boot is irreversible** (OTP key hash; on Pi 5/CM5 it cannot even be tested without burning OTP). Enable it
   only on a dedicated prototype board? Recommended: yes, and keep development boards unsigned.
2. **Key escrow** for the sealing key file: without an offline escrow copy a dead controller makes a sealed volume
   unreadable. Escrow or not, and where?
3. **Board purchase** for C1 (Raspberry Pi 4 Model B, or Pi 5) plus a USB SSD — the first buildable-now step; also
   needed for the P10 Pi acceptance.
4. **First physical DNA test**: an external-service pilot (oligo pool from a vendor + sequencing service or a MinION in
   a partner lab) would give the first category E result; budget and partner are founder decisions.
5. Unverified points that a purchase or a vendor contact would settle: Pi 5 gadget speed (USB 2.0 assumed), ROCK 5B USB
   3 device mode, `usb_f_tcm` on `dwc2`, Flongle on the MinION Mk1D (not in ONT's compatibility list), Opentrons HTTP
   API details, Biomemory product availability, IDT oPools page (not loadable).

## Repository hygiene

Only the four documents above were added; `docs/V14_STATE.md` was not edited; no code, test or result file changed.
No commands were run against hardware; the host was used for web research and one text extraction of a downloaded
specification PDF.
