# Source and attribution

These files are **outputs** (summaries, run report, reconciliation) of running logpipe on the public
`OpenStack_2k.log` sample from [Loghub](https://github.com/logpai/loghub) (OpenStack infrastructure log, generated on CloudLab).
The raw log file itself is not included in this repository.

Loghub datasets are freely available for research or academic work. If you use them, please cite:
Jieming Zhu, Shilin He, Pinjia He, Jinyang Liu, Michael R. Lyu. *Loghub: A Large Collection of System Log Datasets for
AI-driven Log Analytics.* IEEE International Symposium on Software Reliability Engineering (ISSRE), 2023.

Reproduce: download `OpenStack_2k.log` from the Loghub repository into `data/real/` and run

    python -m logpipe run --input data/real/OpenStack_2k.log --db out/openstack.db --out out_openstack
