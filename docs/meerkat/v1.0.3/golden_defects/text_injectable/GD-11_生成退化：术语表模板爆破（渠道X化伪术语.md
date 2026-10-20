# GD-11 生成退化：术语表模板爆破（渠道X化伪术语）

- 缺陷分类：质量退化
- 预期检出层：code
- 基线证据：baseline/report_merged.md L5665/L5697-5699（约200条，占第13章2/3）
- 判定提示：退化检测：重复n-gram/模板爆破应命中

## 注入素材

```text
| 渠道孔子化 | Confucius | 销售渠道的孔子化 |
| 渠道NFT化 | Channel NFT | 销售渠道的NFT化 |
| 渠道刘敬化 | Liu Jing | 销售渠道的刘敬化 |
| 渠道刘敬化 | Liu Jing | 销售渠道的刘敬化 |
| 渠道刘敬化 | Liu Jing | 销售渠道的刘敬化 |
```
