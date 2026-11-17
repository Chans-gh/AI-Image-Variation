## 改了什么

<!-- 一两句话说明动机与结果，不要只贴 diff。 -->

## 自检（提交前请逐项确认）

- [ ] `python shared/check_baseline.py` → 238/238（**基线包只读，未改动**）
- [ ] `python skills/image-variations/tests/run_all.py` → 10 个套件全绿
- [ ] 新增能力同时有**正例与负例**测试
- [ ] 没有内联阈值（一律走 `config/iv.config.json`）
- [ ] 没有新增/自造错误码（一律取 `references/error-registry.json`）
- [ ] 没有静默降级（能力/预算/校验不通过必须显式报码）
- [ ] 密钥只走 `secret://` / `env:` 引用，产物与日志里不含密钥、原图、私有路径
- [ ] 若是发布相关改动：已用 `git clone` 到空目录跑过一次全量测试

## 影响面

<!-- 涉及哪个阶段/模块；是否需要改动契约或基线（后者需单独确认）。 -->
