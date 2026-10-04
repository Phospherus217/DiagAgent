# Public diagnostic sample

This is an exported run of `MATCHED-PARAMETER --mode constrained_recovery`, containing its original failed run only. Actions ran through the existing mock runtime and Pillow backend: the image was resized to 256x256 and exported, while the task required 512x512. The recorded first failure is step 2; the actual PNG fails the size requirement.

The screenshots are generated mock observations, not desktop captures. Files include the actual trace, events, observations, PNG, evaluator output, diagnosis and evidence graph. Local absolute paths were replaced with relative references; diagnosis hashes were recomputed from that portable copy. The exporter did not change action values, step results or artifact bytes. This is a fixture, not frozen or real-GIMP evidence.

Load this directory with `load_trace`, `classify_bundle` and `build_evidence_graph` as shown in the repository README. To produce complete original/recovery/session evidence, run the case CLI into a fresh directory.
