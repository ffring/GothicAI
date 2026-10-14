# Service/combat gate separation: focused validation

175 targeted tests passed on dev-ubuntu in the separately frozen `stage8-service-separation-v1` snapshot. Test wall time: 0.33 s; process wall: 0.46 s; CPU user/system: 0.41/0.03 s; maximum RSS: 48,152 KiB. Four protected service identities and states remained unchanged. No model forward, engine, service workload, load or screening was started.

The snapshot manifest was written before the test invocation, binds 123 files and has SHA-256 `d0033f2e0f41ab4d48728ab69b4f9451902960712b962fa368de8672bcb3408f`. All bound files were rehashed unchanged after completion. The saved subset contains the executed changed sources/configs/tests and compact execution receipts; it contains no engine journals or weights. The complete dependency snapshot remains at `/home/kaifat/gothic-ai/body/data/stage8-service-separation-v1`.

The shared body lock serialized pytest; the user unit used CPUQuota=200%, MemoryMax=6G, Nice=10, TasksMax=128, RuntimeMaxSec=180 and an empty CUDA_VISIBLE_DEVICES. Exit status 0. See [summary](summary.json), [JUnit](junit.xml), [manifest](manifest.json), [resources](resources.txt) and [unit log](launcher.log).

The tests verify explicit standalone service reporting, unchanged legacy candidate-service coupling when no role is declared, exact matching preregistration/config roles, unchanged 10% budget and 20 samples, and independent physical control/latency failure. This establishes code validation only. Calibration failed; no new corpus, training, 279-episode child or combat gate was opened.
