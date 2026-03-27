# Scripts

## 文件

- `generate_all_icons.py`：扫描解压后的 npm 包，生成全量图标和别名图标
- `aliases.json`：只保存别名映射与排除规则，不负责全量抓取清单

## 本地测试

```bash
npm pack @lobehub/icons-static-png@latest
tar -xf lobehub-icons-static-png-<version>.tgz -C .tmp/upstream
python scripts/generate_all_icons.py --package-dir .tmp/upstream/package --max-files 12
```

## 常用参数

- `--package-dir`：解压后的 `package` 目录
- `--max-files`：仅生成前 N 个 light / dark 图标，便于本地测试
- `--skip-aliases`：跳过 `IconSet/aliases` 生成
