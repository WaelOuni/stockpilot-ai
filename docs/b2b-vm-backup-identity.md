# Reconcile the recovered VM's backup identity

This adapter is a proposed operator correction, not an application release or a
deployment attestation. It must be reviewed and validated before installation on
StockPilot-B2B. No change to the historical operator, package manifests, backup
manifests, private configuration or Docker resources is needed to prepare it.

## Narrow compatibility boundary

The installed R13 backup operator pins a BFF container removed during the R16/R18
recovery. R18 has a separate, sealed contract and a completed journal for the
replacement BFF at revision `b018bbf1c346bbcec11b4347e41175fad176f168`. Its normal
read-only verification succeeds, but the backup operator has no generation input.
The local-demo upgrade planner targets a different installation and revisions;
it must not be repurposed by replacing strings.

`scripts/b2b_vm_backup_identity.py` pins the observed historical backup, R13
package and R18 attester hashes. It calls the unchanged R18 package, initial and
final gates in an isolated interpreter while the parent holds the normal backup
lock. All transitive recovery seals remain mandatory. A missing/incomplete R18
journal, unexplained resource drift or additional recreation is a refusal.

Only the BFF ID may differ from the historical backup, and that ID must match
R18's normal attestation. A new generation records the six container identities,
configuration and host hashes, mounts, persistent volume metadata and filesystem
identities, network identities and private-file hashes/permissions. Raw Docker
environment values never enter the generation. The generation is private, new,
fsynced, reread and SHA256-bound; no existing file is overwritten.

The adapter replaces only the legacy **ID-only inventory reader in memory** with
the generation-aware reader. It does not edit `EXPECTED` or the installed source.
Every inventory call checks the generation. The original capacity, OCI archive,
database/pending-operation, quiescence, capture and source recovery logic remains
in the pinned operator. Its original capture entrypoint acquires the same lock
before the first attestation and holds it through recovery. Preflight and
generation creation acquire that lock in the adapter. Python optimization and
Docker environment/context overrides are refused.

## After code review and validation only

Install the exact reviewed adapter as a root-owned private file in the existing
operations directory; verify its SHA256 against the reviewed commit. Do not
replace the sealed R13/R18 files. Under the existing VM authorization, use:

```sh
python3 -B /opt/stockpilot/operations/b2b_vm_backup_identity.py plan \
  --generation /opt/stockpilot/backups/vm-backup-identity-UNIQUE-ID.json

python3 -B /opt/stockpilot/operations/b2b_vm_backup_identity.py preflight \
  --generation /opt/stockpilot/backups/vm-backup-identity-UNIQUE-ID.json \
  --sha256 DIGEST_RETURNED_BY_PLAN

python3 -B /opt/stockpilot/operations/b2b_vm_backup_identity.py capture-authorized \
  --generation /opt/stockpilot/backups/vm-backup-identity-UNIQUE-ID.json \
  --sha256 DIGEST_RETURNED_BY_PLAN
```

The original capture restarts its writers and initially reports
`source_recovered=false`. Do not call the historical standalone recovery verifier:
it imports the obsolete ID reader again. Use `verify-capture` with the same
generation/digest and `--backup` set to the newly returned capture directory.
This rechecks all six exact identities, images, private configuration, healthy
writers, database/pending-operation facts, verified HTTPS/OIDC and all twelve
archive hashes/sizes before recording source recovery. It preserves the original
operator result and updates only the result in this fresh capture. Captures older
than the generation, historical IDs and mismatching images/configuration are
refused. Restart timestamps are deliberately not reused from R18 in this phase:
the authorized capture has restarted the same containers, not recreated them.

For `restore-authorized`, provide the same generation/digest and `--backup`, plus
`--network-plan`, `--host-inventory` and `--restore-request`, all private files in
that new capture directory. Their format and validation remain owned by the
sealed `restore_network.py` and `restore_target.py`. The host network inventory
must be complete and at most 120 seconds old when the allocation gate checks it;
never reuse the historical inventory. The attempt must name absent resources and
explicit, unoccupied internal subnets. The original source-preservation, isolated
ownership, full PostgreSQL metadata/ACL/role, logical data and activation restore
gates all still run. Their source inventory provider is generation-aware.
Failure leaves the isolated attempt's evidence intact, without automatic cleanup.
Proof details stay in private files; the console receives only the result.

The historical Keycloak index profile also binds an old backup manifest. A fresh
capture must not reuse that provenance. Before creating restore resources, the
adapter qualifies the **unchanged historical metadata contract** against the new
capture and live source using the sealed index comparator: identical image,
server, collation, metadata and all four qualified index semantics are required.
Only then does it write a new private `qualified-keycloak-index-profile.json` in
the current capture, binding both new manifest hashes, the predecessor profile,
source container and identity generation. The old profile and archives remain
intact. Unknown metadata or index changes are refused, not auto-qualified.

The restored Keycloak cluster uses the same sealed comparator against this fresh
profile. If PostgreSQL reconstructs one of the four already-qualified internal
index attribute aliases, the proof includes the full private metadata/index
witness. The activation check verifies that witness, image and both new manifest
hashes and runs the same semantic comparator again. Raw-equal metadata retains
the original activation check; other services cannot use the alias exception.
Failure evidence records only exception type and stack function/line pairs,
never exception strings, SQL bodies, credentials or subprocess output.

The identifiers above are placeholders, not executable approval. Retain the
generation and its digest with the new capture. A failed gate is a stop: do not
edit a generation, re-pin an unexplained difference, reuse older archives as a
new capture, or weaken an original recovery assertion.

This is deliberately a bridge from the **completed R18 state to one fresh
capture**, not a general restart/recreation tool. R18 also pins start timestamps;
after a successful capture restarts its writers, a later capture requires a new
reviewed lifecycle contract rather than another automatic reconciliation.

## Evidence still required before deployment

Unit tests use synthetic identities, fake Docker responses and temporary files;
they do not prove backup/restore on the VM. After the corrected preflight succeeds,
require a current paired capture, SHA256 checks, independent full-fidelity restore
into separate containers/volumes/networks, logical comparison and source recovery
attestation. Do not use active volumes as restoration targets. A successful plan,
preflight, CI run or archive read is not proof of database recovery.

Only `restore-authorized` invokes the original database restoration into its
explicitly isolated target. The adapter does not change active Keycloak settings,
enable refresh, deploy API/Web or qualify application rollback. Those steps remain
gated by the recovery proof and separate application checks.
Local root and the Docker daemon are trusted; checksums are integrity witnesses,
not signatures defending against a compromised host administrator.
