# Examples

All examples use the byte-locked inputs installed with FinReplay OS. They are safe to run offline
and do not download upstream data.

```bash
python examples/svb_offline_demo.py --output ./out/svb-example
python examples/catalog_filter.py
python examples/capability_paths.py
```

The notebook [`point_in_time_vs_revised.ipynb`](point_in_time_vs_revised.ipynb) walks through the
distinction between economic time, knowledge time, and later revisions using the bundled scenario
catalog. It does not embed a claimed investment result.

For source-specific live access, start with the [adapter authoring and safety guide](../docs/adapter-authoring.md).
