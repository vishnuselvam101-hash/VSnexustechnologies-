# V14 boot architecture

Phase 11 design document (master directive §18, and §17.1 for the electronic metadata/boot region;
[V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P11). Companion of [V14_EXTERNAL_CONTROLLER.md](V14_EXTERNAL_CONTROLLER.md),
whose decision (stage C1: a Raspberry Pi 4 class board as a USB Mass Storage, Bulk-Only device) fixes the platform.

**Status: design only. No board was provisioned, no OTP bit was written, no image was signed, nothing was booted.
No statement here says that any host has booted from a VNX device, and none says that DNA supplied boot data.**
Sources: §9 (tag `[Bn]`; `[Sn]` refers to the sources of the controller document). Consulted 2026-10-18. "V" in §9 =
text read; "snippet" = search result only; "not verified" where we could not confirm.

There are two boot problems, kept apart:

* **A. The controller boots** — the board's own chain from ROM to a running `vnxd` (§2–§6).
* **B. A host boots from the controller** — host firmware (UEFI) reads a bootloader through the device (§7).

## 1. Rules carried over from V14

* Boot never bypasses VNX mapping, transactions or integrity (directive §15): a host's firmware reads go through the
  same `IoQueue → Volume` path as any read.
* Boot data is **electronic**. Firmware has timeouts of seconds; a DNA retrieval takes hours to days
  ([V14_PHYSICAL_DNA_DEVICE.md](V14_PHYSICAL_DNA_DEVICE.md)). A boot region is therefore always on an electronic
  backend (directive §17.1); a DNA archive of it may exist, but it is never what the firmware read.
* Keys follow [V14_SECURITY_MODEL.md](V14_SECURITY_MODEL.md): the sealing master key is a generated key file, read
  only by the sealing worker; `vnxd` never holds it.

## 2. Platform

C1 reference board: Raspberry Pi 4 Model B (USB-C device mode, [S14]); alternatives Pi 5, CM4/CM5 on a carrier
([S16]). The EEPROM bootloader exists on "flagship models since Raspberry Pi 4B" and "Compute Module models since CM4"
[B1]. Differences that matter:

| | Pi 4 / CM4 (BCM2711) | Pi 5 / CM5 (BCM2712) | source |
|---|---|---|---|
| firmware stage | EEPROM bootloader → `start4*.elf`/`fixup4*.dat` → kernel | EEPROM bootloader loads the kernel directly | B1 |
| secure boot signs | `boot.img` (bootloader EEPROM signing not required) | `boot.img` **and** the EEPROM bootloader | B2 |
| test secure boot without burning OTP | possible | not possible (OTP changes are irreversible) | B4, B5 |
| NVMe boot | CM4 only | Pi 5, CM5 | B3 |

## 3. Board boot chain (A)

```
 boot ROM (immutable; holds a Raspberry Pi public key; checks Raspberry Pi-signed firmware)     [B2]
   → (recovery.bin if present: EEPROM update path)                                                [B1]
   → EEPROM bootloader in SPI flash; BOOT_ORDER chooses SD / USB-MSD / NVMe / network / RPIBOOT   [B3]
   → [secure boot] boot.img (FAT ramdisk) verified against boot.sig                                [B2][B4]
   → Linux kernel + initramfs (from boot.img)
   → initramfs: unlock the LUKS root with the OTP device key (optional)                           [B2][B6]
   → systemd: vnxd nbd attach (journal recovery) → gadget bind (V14_EXTERNAL_CONTROLLER §5.6)
```

`BOOT_ORDER` is read nibble by nibble from the least significant end; codes include 0x1 SD, 0x4 USB-MSD, 0x6 NVMe,
0x3 RPIBOOT, 0xf RESTART; default 0xf41 [B3]. For the controller: boot from on-board storage only (SD or eMMC, NVMe on
Pi 5/CM5), **never** from USB-MSD, so that the host-facing USB-C port cannot become a boot source of the board. Whether
the device-mode USB-C port is also used by RPIBOOT recovery (§6.3) is the point of that mode: RPIBOOT "using USB
device mode from the USB OTG port" [B1].

Startup ordering inside Linux: the gadget is bound to the UDC only after `vnxd` reports `attached` (after journal
replay) and health is `ok` or `degraded` (then `ro=1`); with `failed` the gadget is not bound.

## 4. Secure and measured boot options (A)

### 4.1 Secure boot (signed boot)

Documented by Raspberry Pi [B2][B4][B5][B7]:
* Once enabled the board "can only run code signed by the customer's private key".
* Key: RSA-2048; OTP rows 47–54 hold the SHA-256 of the customer public key, row 55 the secure-boot flags.
* With `SIGNED_BOOT=1` only `boot.img` is loaded, verified against `boot.sig`
  (`rpi-eeprom-digest -k key.pem -i boot.img -o boot.sig`).
* `program_pubkey`, `revoke_devkey` and `program_jtag_lock` are "irreversible" and are applied only through RPIBOOT when
  the EEPROM is flashed; "This operation cannot be undone and the key hash cannot be changed" [B4].
* Raspberry Pi's `rpi-sb-provisioner` supports CM5, CM4, CM4S and Pi 5/500/500+ [B7].

Design choice: **secure boot is off on development boards** and is enabled only on a dedicated prototype after a
founder decision, because it is irreversible and binds the board to one signing key forever. The signing key is kept
offline (not on the lab host, not in the repository).

What the chain covers: everything inside `boot.img` (kernel, initramfs, `vnxd` binary and the Python sealing worker
if they are placed in the initramfs or on a dm-verity root whose root hash is inside `boot.img`). dm-verity is our
proposal; it is standard Linux, not part of the Raspberry Pi documentation, and no root hash layout is specified yet.

Not found in the Raspberry Pi documentation we read: an anti-rollback counter for `boot.img` (not verified either
way). Without one, an attacker with physical access can boot an older correctly-signed image. Mitigation (ours):
`vnxd` refuses a journal whose format version is newer than it understands (VERSION_MISMATCH), so an old image
cannot write an old format over a new volume; it can still run old code.

### 4.2 Measured boot

* The Pi has no TPM. SPI TPM 2.0 add-on boards exist: firmware overlay `tpm-slb9670` for "Infineon SLB9670 …
  add-on boards … available as 'Iridium SLB9670' by Infineon and 'LetsTrust TPM' by pi3g"; `tpm-slb9673` is the I2C
  variant [B8].
* We found no Raspberry Pi statement that the boot ROM or EEPROM bootloader measures anything into a TPM; the vendor
  boot and security documentation does not mention measured boot [B2] (absence, not a vendor statement). An academic
  paper (snippet) proposes signed U-Boot as the root of trust for measurement on Pi 4 [B9].
* Therefore measured boot on this platform could start at the earliest at the kernel (e.g. Linux IMA extending a TPM
  PCR) — **the firmware stages are not measured**. This is classified **custom**; it is not needed for C1.

### 4.3 Other C2/C3 boards (one line each, snippets)

NXP i.MX 8M: HAB secure boot with a fused SRK hash and a CSF signature [B10]. Rockchip RK3588: BootROM checks the
image key against an OTP eFuse hash; no key revocation or rotation (Pengutronix) [B11]; the ROCK 5B fuse flow has been
reported unreliable (forum) [B12].

## 5. First-boot provisioning and key handling (A)

Done once per board, offline, by the operator (not automated in V14):

1. Flash the signed image (or the unsigned development image) through RPIBOOT/`usbboot` [B4].
2. (Prototype only) program the customer public-key hash and secure-boot flags [B2][B4].
3. Optionally write the 256-bit OTP device-specific private key (`rpi-otp-private-key`; BCM2712 has 512 bits) [B6].
   It is irreversible, readable by software through the firmware mailbox (`/dev/vcio`, the `video` group on Raspberry
   Pi OS), and "Raspberry Pi computers do not have a hardware protected key store" [B6]. Its documented use is to
   unlock a LUKS root in the initramfs [B2].
4. First start of the service creates the volume (`vnxd nbd create`) and the **sealing key file**: generated (64 hex
   characters), mode 0600, owned by the worker user, on the LUKS root — exactly the key-file rules of
   [V14_SECURITY_MODEL.md](V14_SECURITY_MODEL.md) §3. The OTP key protects it at rest (via LUKS); it never derives the
   sealing key directly, so replacing a board does not lose data if the key file was escrowed.
5. Key escrow: an encrypted copy of the key file to offline media at provisioning. Without it, a dead board makes a
   sealed volume unreadable. Whether to escrow is a founder decision (§8).
6. Record board serial, OTP state, image hash, volume `device_id` in the provisioning log (audit log).

Consistency with the security model: the key file never leaves the board except as the escrow copy; the management
token file (P4 §4) is created the same way; nothing secret is in `boot.img` (it is signed, not encrypted).

Limit (stated): the OTP key is software-readable, so a root compromise of the running board yields it; and a stolen
board with secure boot **off** boots any image, so LUKS-with-OTP only protects against removal of the storage medium,
not against theft of the whole board.

## 6. Update, rollback and recovery (A)

### 6.1 A/B image update

Raspberry Pi `tryboot` [B13]: `sudo reboot '0 tryboot'` boots once with `tryboot.txt` (or `tryboot.img` under secure
boot), the flag is cleared before start, so a crash reverts. `autoboot.txt` with `boot_partition=N`, a `[tryboot]`
section and `tryboot_a_b=1` switches whole partitions; the update is committed by changing `autoboot.txt`.

VNX procedure (ours): write the new image to the inactive slot → `tryboot` → the new slot runs `vnxd` against the
volume **read-only** health check (journal opens, scrub sample passes, gadget not bound) → commit (`autoboot.txt`)
→ normal reboot. Any failure → plain reboot → old slot. RAUC, SWUpdate and Mender are generic frameworks that could
drive this (Bootlin reports RAUC had no official `tryboot` backend; a custom one uses `autoboot.txt`) [B14].

### 6.2 Volume format across updates

The journal and backend are outside the A/B slots. An update never rewrites them; a new `vnxd` only reads formats it
knows; downgrades that would meet a newer format are refused (VERSION_MISMATCH) and the board stays on the slot that
works.

### 6.3 Recovery

* Bootloader EEPROM: updated through `recovery.bin` [B1]; its detailed rollback behaviour was not researched.
* Bricked board: RPIBOOT over the USB device port from a recovery host (`usbboot`) [B1][B4].
* Lost signing key with secure boot on: the board cannot run new images (irreversible) — the data survives only if
  the key file was escrowed and the backend storage is readable elsewhere (sealed objects need the key, §5).

## 7. A host booting from the VNX device (B)

### 7.1 Firmware-visible reads

UEFI exposes disks to boot loaders through `EFI_BLOCK_IO_PROTOCOL` (and Block I/O 2) [B15][B16]. EDK2's
`UsbMassStorageDxe` drives USB mass storage over CBI0, CBI1 and **BOT** only (no UAS in upstream EDK2) [B17]; NVMe
boot goes through `NvmExpressDxe` and `EFI_NVM_EXPRESS_PASS_THRU_PROTOCOL` [B16]. So the C1 device (BOT) is on the
firmware's supported path; a UAS-only device would not be on EDK2's path (vendor firmware not verified). Firmware reads
are ordinary SCSI READs (§5.2 of the controller document) — **nothing in the device knows they come from firmware**.

### 7.2 Boot-region layout

```
 LUN 0  (volume "boot", backend image = electronic, read-only to the host unless updating)
   GPT
   EFI System Partition (FAT) : \EFI\BOOT\BOOTAA64.EFI / BOOTX64.EFI (signed loader), loader config
   OS boot partition          : kernel, initramfs
 LUN 1  (volume "data", any backend incl. dna-sim; never needed to boot)
```

* Two volumes, two LUNs (the gadget supports up to 8 [S2]). The boot volume is pinned to an electronic backend so
  every firmware read is served from the board's storage with the V14 SHA-256 check; it never depends on DNA.
* Required electronic bootstrap metadata (directive §17.1): the volume journal (map, generation SHA-256s), the backend
  binding marker, the boot manifest (§7.3). All on the board, never on DNA, never visible to the host.

### 7.3 Metadata discovery and manifest verification

* Discovery: the host firmware discovers the ESP by GPT type GUID (standard UEFI behaviour [B15], spec page not
  opened). The device needs no discovery protocol of its own.
* Two independent integrity layers: (1) host side — UEFI Secure Boot verifies the loader against `db`, rejecting
  `dbx` entries (`dbx` takes precedence; KEK governs db/dbx; PK governs KEK) [B18]; (2) device side — every extent read
  is checked against the journal SHA-256 (CORRUPTION → SCSI error, never other bytes). Proposed (ours, not built): a
  boot manifest = list of (LBA range, SHA-256) of the ESP signed with the provisioning key, checked by `vnxd` at
  start; mismatch → boot LUN not exported.

### 7.4 Controller initialisation and timing constraints

The board must be ready before the host firmware gives up on the USB device. The Pi boot to `vnxd attached` takes
seconds (not measured); the gadget is bound only afterwards, so the host sees "no device" rather than an unready one,
and the host firmware's USB enumeration timeout decides whether it waits (not researched per firmware). Practical
rule: power the controller first, then the host.

### 7.5 VM test architecture (designed, not run)

Validate the software path first (directive §18): QEMU with OVMF (EDK2 for VMs) and a `usb-storage` device ("the
classic bulk-only transport") on an XHCI controller [B19], whose drive is the P8 Stage B export of `vnxd nbd serve`
(`nbd+unix://?socket=...`, [B20]) over a disposable image volume holding a GPT + ESP with a test EFI application.
Pass criteria: OVMF lists the device, starts the EFI application, the application reads a known file and prints its
SHA-256; then the same with a corrupted extent → the firmware sees a read error, never other bytes. `usb-uas` exists
in QEMU too [B19], which lets the UAS path be checked against OVMF (expected: not bootable by upstream EDK2, §7.1).

### 7.6 Future physical tests

After C1 is assembled: a spare x86 or ARM64 UEFI machine boots the test EFI application from the board; then a
Linux install on LUN 0. Only after that may any document say a host booted from the VNX controller — and it will
still say the boot data was electronic.

### 7.7 Classification

| item | class | why |
|---|---|---|
| board boot chain + BOOT_ORDER (§3) | buildable-now | vendor firmware and documented settings [B1][B3] |
| signed boot (§4.1) | buildable-now, irreversible | vendor tooling [B4][B7]; founder decision required |
| measured boot (§4.2) | custom | SPI TPM add-on [B8] + kernel-level measurement; firmware stages unmeasured |
| provisioning + key handling (§5) | buildable-now (procedure) | vendor OTP tools [B6] + the existing key-file rules |
| A/B update + rollback (§6) | buildable-now | `tryboot` [B13]; the VNX health gate is a script |
| boot manifest check in `vnxd` (§7.3) | custom | new code |
| VM boot test (§7.5) | buildable-now | QEMU + OVMF + `vnxd nbd serve` [B19][B20] |
| physical host boot from the controller (§7.6) | custom (needs the C1 board) | not assembled |

## 8. Failure modes

| failure | effect | handling |
|---|---|---|
| signed image corrupt / wrong signature | board does not boot that image | tryboot reverts to the old slot; RPIBOOT recovery |
| update slot boots but `vnxd` cannot open the volume | no gadget bound | not committed; reboot → old slot |
| journal damaged in both mirrors | volume refused (CORRUPTION) | gadget not bound; management reports; operator restores |
| sealing key file missing / wrong | UNAUTHORIZED at open | gadget not bound; escrow copy restores |
| board dead (its OTP key goes with it) | LUKS root unreadable on another board | escrowed key file + backend storage moved to a new board |
| power cut during boot-volume update | only flushed writes are durable (P2) | boot volume updated as a whole new generation set; old ESP stays readable until commit |
| host firmware times out before gadget bind | host boots something else | power order; documented |
| UAS-only host firmware path | not bootable (upstream EDK2) | BOT kept on the boot LUN |
| backend for the data LUN is DNA and not staged | data LUN not ready | never needed for boot (§7.2) |

## 9. Sources

| tag | source | supports | read |
|---|---|---|---|
| B1 | Raspberry Pi documentation, boot EEPROM and boot flow (`documentation/asciidoc/computers/raspberry-pi/bootflow-eeprom.adoc`, commit ddea98e, 2026-10-13): https://github.com/raspberrypi/documentation ; rendered https://www.raspberrypi.com/documentation/computers/raspberry-pi.html | EEPROM bootloader models; `recovery.bin`; Pi 5 loads kernel directly; RPIBOOT via USB device mode | V |
| B2 | Raspberry Pi documentation, security architecture (`documentation/asciidoc/computers/security/security-architecture.adoc`), same repository | chain of trust; BCM2712 signs the EEPROM bootloader; LUKS with OTP secret | V |
| B3 | Raspberry Pi documentation, `eeprom-bootloader.adoc` and `boot-nvme.adoc`, same repository | BOOT_ORDER codes and default 0xf41; NVMe boot models; `PCIE_PROBE` | V |
| B4 | Raspberry Pi `usbboot` secure-boot recovery (Pi 4/CM4): https://github.com/raspberrypi/usbboot/blob/master/secure-boot-recovery/README.md ; config.txt `boot.adoc` (secure boot properties) | irreversible OTP programming; SIGNED_BOOT; `program_pubkey`/`revoke_devkey`/`program_jtag_lock` | V |
| B5 | Raspberry Pi `usbboot` secure-boot recovery for BCM2712: https://github.com/raspberrypi/usbboot/blob/master/secure-boot-recovery5/README.md | Pi 5/CM5: no test without irreversible OTP changes | V |
| B6 | Raspberry Pi documentation, OTP bits (`otp-bits.adoc`), same repository | rows 47–54 key hash, 55 flags, 56–63 256-bit device private key (512 on BCM2712); `rpi-otp-private-key`; readable via mailbox; no hardware-protected key store | V |
| B7 | Raspberry Pi documentation, Secure Boot Provisioner (`security/secure-boot-provisioner/secure-boot-provisioner.adoc`); whitepapers RP-003466-WP https://pip.raspberrypi.com/documents/RP-003466-WP and RP-004651-WP https://pip.raspberrypi.com/documents/RP-004651-WP | supported models; vendor whitepapers (PDFs not opened) | V (page) / not opened (PDFs) |
| B8 | Raspberry Pi firmware overlays README: https://github.com/raspberrypi/firmware/blob/master/boot/overlays/README | `tpm-slb9670`, `tpm-slb9673` overlays; Iridium SLB9670, LetsTrust TPM | V |
| B9 | ECCWS paper on Pi 4 trusted boot: https://papers.academic-conferences.org/index.php/eccws/article/download/4827/4389/17442 | Pi 4 bootloader "supports Secure Boot only"; signed U-Boot as measurement root | snippet |
| B10 | Zondax, i.MX8M secure boot notes: https://docs.zondax.ch/tee-signer/Secure%20Boot/SecureBoot-iMX8M | HAB, SRK hash in fuses, CSF | snippet |
| B11 | Pengutronix, RK3588 secure boot (2026-06-19): https://www.pengutronix.de/en/blog/2026-06-19-rk3588-secure-boot.html | OTP key hash; no revocation/rotation | snippet |
| B12 | Radxa forum, secure boot on ROCK 5B: https://forum.radxa.com/t/secure-boot-on-rock-5b/14498 | fuse flow reported unreliable | snippet |
| B13 | Raspberry Pi documentation, `config_txt/autoboot.adoc` (tryboot, `tryboot_a_b`), same repository | A/B with tryboot and autoboot.txt | V |
| B14 | Bootlin, RAUC on Raspberry Pi: https://bootlin.com/?p=19769 ; RAUC https://rauc.io ; SWUpdate https://sbabic.github.io/swupdate/ ; Mender https://mender.io | update frameworks; no official RAUC tryboot backend at the time | snippet / homepages not fetched |
| B15 | UEFI Forum specifications: https://uefi.org/specifications ; Media Access protocols (UEFI 2.11 §13): https://uefi.org/specs/UEFI/2.11/13_Protocols_Media_Access.html | Block I/O protocol; GPT/ESP | not opened (HTTP 403); corroborated by B16 |
| B16 | EDK2 `MdePkg/Include/Protocol/BlockIo.h`, `BlockIo2.h`, `NvmExpressPassthru.h`; `MdeModulePkg/Bus/Pci/NvmExpressDxe/` (tree c141577): https://github.com/tianocore/edk2 | Block I/O "as defined in the UEFI 2.0 specification"; NVMe pass-thru "introduced in UEFI 2.5"; NVMe boot driver | V |
| B17 | EDK2 `MdeModulePkg/Bus/Usb/UsbMassStorageDxe/` (`UsbMassImpl.c`, `UsbMass.h`): https://github.com/tianocore/edk2/tree/master/MdeModulePkg/Bus/Usb/UsbMassStorageDxe | transports CBI0/CBI1/BOT only, no UAS | V |
| B18 | UEFI Secure Boot (UEFI 2.11 §32): https://uefi.org/specs/UEFI/2.11/32_Secure_Boot_and_Driver_Signing.html ; revocation list https://uefi.org/revocationlistfile | PK/KEK/db/dbx roles | snippet (403) |
| B19 | QEMU docs, USB emulation: https://www.qemu.org/docs/master/system/devices/usb.html | `usb-storage` bulk-only; `usb-uas`; XHCI supports USB 1.1/2.0/3.0 | V |
| B20 | QEMU docs, block drivers (NBD): https://www.qemu.org/docs/master/system/qemu-block-drivers.html | `nbd+unix://?socket=` URIs | V |
