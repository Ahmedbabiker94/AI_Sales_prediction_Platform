# R7-05B1 — protected successful B3 producer issuance

This additive boundary invokes the original accepted B3 service. It retains the
actual successful `ExecutionOutcome` and its exact returned result object. It
does not accept detached results, add a native predictor, issue execution grants,
write forecasts or change R7-05A declarations.

## Trusted host composition

The host constructs `SuccessfulB3ProducerService` with the original B3 service
and a private operator object. `produce()` requires that exact operator and an
independently issued original B3 capability. It calls the original B3 class
method captured at import, not a caller executor or instance method alias.
An operator string, public producer ID, receipt digest or consumed capability
alone establishes no successful origin.

The original B3 provider verifies the retained M6 candidate, original B1 native
envelope and B2 inputs. The producer requires the same owner record before and
after execution. Following an actual successful return, it also checks the live
original consumed grant, reservation, owner and envelope identities. A failed
but consumed attempt never receives a receipt. This uses the accepted private
in-process composition seams without changing B1/B2/B3.

The host must pin the producer instance used by future consumers. Constructing
another producer does not give its receipts authority at that pinned instance.
Python reflection, replacing private functions/registries, modified trusted test
code or a hostile Docker daemon are outside this boundary. It is not durable IAM,
a Python sandbox or cryptographic authentication.

## Contract and encoding

`SuccessfulB3Receipt` is an opaque nonserializable original handle. Its protected
record retains the original owner, B1/B2 envelopes, successful outcome/result and
consumed grant. Original outcome/result canonical encodings are pinned separately
to detect mutation even when Python object identity remains unchanged.

The version 1 declaration is a detached immutable view. It embeds the accepted
B3 result and complete request/grant: candidate/family, native artifact ID/digest,
B2 input/representation/frozen-state identities, canonical snapshot, dataset,
tenant, complete series, publication/cutoff/origin and controlled H1 purpose.
Ordered M2 points from the actual B2 representation additionally bind each row's
target interval, dimensions, metric/unit/currency and series. Each prediction is
encoded as exactly four little-endian float32 bytes, including signed zero.
Shape, physical dtype and a versioned result-set digest are checked together.

Canonical serialization and hashing reuse accepted contracts. A result-set digest
binds the B3 result ID and ordered scope/bit rows. A receipt ID binds that content,
producer instance ID, issuance ordinal and lifetime. Equal B3 public result values
can recur; they are not unique authenticated execution receipts. Handles and live
retention distinguish actual issuances. No secrets, native payload bytes, private
model objects or training rows enter the public declaration.

## Lifetime, revocation and failure

Issuance permits a 1–60 second receipt lifetime and requires an explicit operation
deadline. Bounded monotonic elapsed time, nondecreasing wall time, receipt count
and declaration byte limits fail closed. Records, including revoked/expired ones,
are retained up to the bound; there is no eviction, restoration or reset API.

Verification is repeatable and performs no B3 execution, source capture, model
load or database operation. Every verification requires the original handle,
exact owner/envelopes and expected request. It returns a fresh declaration copy,
never a mutable alias to the protected originals. Public declarations and equal
foreign results cannot be supplied as receipts.

`revoke()` permanently replaces a receipt record with an inactive record;
`invalidate()` permanently disables the issuer. Original M6 discard, envelope
tampering or explicit B3 grant revocation also makes verification fail. Receipt
expiry is independent of the completed execution window: an unrevoked past
successful fact may remain verifiable after that execution window closes, until
receipt expiry. This never renews or reuses execution permission.

Cancellation and both producer clocks run before every terminal live-record,
grant and owner lookup. Callbacks never run under those locks. Reentrant producer
operations reject rather than wait; revocation has a separate record lock.
No supported callback runs after final validation and before return/publication.
Sequential checks are not atomic leases against external concurrent revocation
after validation. Native preemption remains the disposable worker's responsibility.

Failures raise a fixed `ProducerEvidenceError` with no admitted receipt. An
execution that succeeded but failed producer terminal checks has no receipt;
its consumed B3 grant is not restored or automatically retried.

## Verification scope

The runtime unit module explicitly mocks the private B3 call and provider for
contract/fault testing, while using real frozen M3 source context. Those cases
do not establish native origin. The separate native module reuses the accepted
genuine B3 fixture, actual M6 XGBoost fitting, B1 UBJSON verification and B2
read-only capture/M3 transformation. Passive profiling observes the original B3
return to prove exact retained object identity without replacing its implementation.
Native tests include post-success terminal grant/owner revocation, receipt
revocation, original-handle substitutions and missing-dependency consumption.

All native execution must use the accepted P0 recovered-image launcher with its
explicit opt-in, exact image/manifest checks and unchanged isolation. Synthetic
fixture catalogs exist only in private disposable worker tmpfs; no application
or forecast database is accessed. M7 remains TEST_ONLY_NON_DEPLOYABLE with no
model payload; R7-03/04A remain non-executable; R7-05A remains structural-only.

R7-05B2 still needs separately approved independent write authority, a durable
repository transaction/uniqueness/CAS and correction design, genuine read-only
verification at the write boundary and consumer migration. Receipt verification
grants no persistence, production inference, promotion or synchronization rights.
If a future writer is in another process, a separately reviewed handoff is
required: serializing a declaration does not preserve this original-handle proof.

## Recorded candidate verification

All worker invocations use `tests/native/run_recovered_image.py` with explicit
`--use-validated-recovered-image`; the original default launcher is unchanged.
The complete new contract module passed 68 cases in one invocation. The complete
new native module passed 14 cases in the final 53-case invocation that also ran
39 selected accepted B3 regressions. Its original-return proof observes the
original method's return event; it does not inject a native execution substitute.

| Distinct cases | Passing count |
| --- | ---: |
| New mocked-B3 producer contract/fault cases | 68 |
| New genuine native-context producer cases | 14 |
| Selected accepted B1 native regressions | 27 |
| Selected accepted B3 regressions | 39 |
| Complete accepted B2 and M6 modules | 149 |
| Complete P0 host module | 77 |
| P0 non-model worker smokes and selected R7-03 cases | 28 |
| Selected R7-04A cases | 21 |
| Selected R7-05A cases | 21 |
| Total | 444 |

The native contexts share a real fitted fixture; not every adverse case loads or
predicts. Contract-only mocked calls are not native-origin proof. Earlier
overlapping reruns are excluded. No separate external adversarial case count is
added to these repository cases.

One larger combined R7 regression worker ended with exit 137 after 182 seconds.
Docker recorded no OOM event and did record cleanup. This is consistent with the
unchanged CPU hard limit of 180 seconds; the exact kernel cause was unavailable
after removal. Its partial output is excluded. The selected smaller R7 batches
above passed without changing resource limits. No whole-repository collection,
dependency installation or persistent model publication was performed.
