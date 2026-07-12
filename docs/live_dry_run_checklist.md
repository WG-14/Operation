# Live Dry-Run Checklist

## Purpose

Confirm that live mode is safe when `LIVE_DRY_RUN=true`.

Background: This checklist is for validation only and does not permit real orders.

## Startup Checks

- [ ] `OPERATION_ENV_FILE` points to the explicit live env file with live DB and run-lock paths
- [ ] `LIVE_DRY_RUN=true`
- [ ] `LIVE_REAL_ORDER_ARMED=false`
- [ ] Notifier configuration is present and valid
- [ ] `/var/lib/operation/data/live/trades/live.sqlite` backup is reachable
- [ ] `operation.service` is running normally
- [ ] `operation-healthcheck.timer` is enabled
- [ ] `operation-backup.timer` is enabled

## Runtime Checks

- [ ] `sudo systemctl status operation.service`
- [ ] `sudo journalctl -u operation.service -n 100 --no-pager`
- [ ] No healthcheck error is present
- [ ] No halt state is present
- [ ] The service can still recover after restart

## During Dry-Run

- [ ] There is no duplicate execution
- [ ] The run lock behaves normally
- [ ] Reconcile does not report an error
- [ ] There are no unexpected unresolved open orders
- [ ] Notifier delivery works
- [ ] The backup timer runs normally

## Deterministic Chaos Gap

This checklist validates operational live dry-run safety. It does not force
deterministic broker failure families such as:

- `submit_timeout_then_reconcile`
- `broker_reject_under_min_total`
- `partial_fill_then_fee_pending`
- `order_not_ready_then_recovery_required`

Use paper stress execution for deterministic paper lifecycle rehearsal. A
live-dry-run chaos broker or scenario runner is still required before claiming
that live dry-run rehearses these failure paths directly.

## Minimum Conditions for Switching to Real Orders

- [ ] No unexplained error persists over time
- [ ] systemd restart and reboot behavior is normal
- [ ] Healthcheck remains stable during live execution
- [ ] Incident documentation is complete
- [ ] Rollback and restore evidence is ready

## Pass / Fail Criteria

- Pass: all startup, runtime, and minimum conditions are satisfied.
- Fail: any live-safety, recovery, or separation rule is broken.
