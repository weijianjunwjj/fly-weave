"""after-sales replacement 场景的可摄取 demo policy source material（T024）。

这不是 seed 代码里的临时字符串，而是一份真正可摄取的知识源材料：
``seed_demo_data`` 通过 ``policy_ingestion_service.ingest_policy_document`` 把
它 deterministic ingestion 成 ``PolicyDocument`` 与一组 ``PolicyChunk``。

内容仅覆盖当前唯一业务垂直（after-sales replacement），并明确标记为
demo/simulated。字段与 ``PolicyDocumentInput`` 一一对应，各 section 用空行分隔，
因此 deterministic paragraph/section-aware chunking 会为每个 section 产生一个
可追溯到本来源的 chunk。
"""
from policy_documents import PolicyDocumentInput

DEMO_REPLACEMENT_POLICY_DOCUMENT = PolicyDocumentInput(
    business_key="policy-doc-replacement-standard",
    title="标准换货政策（demo/simulated）",
    source_reference="policy-doc://after-sales/replacement-standard/v1",
    is_demo_data=True,
    raw_content=(
        "换货政策总则（demo/simulated）\n"
        "本政策为 Fly-Weave 售后换货场景的模拟政策文档，仅用于演示与测试，不构成真实业务承诺。\n"
        "\n"
        "质量问题定义\n"
        "产品在正常使用中出现性能故障、无法开机、单侧无声、异响、结构开裂等非人为损坏情形，认定为质量问题。\n"
        "\n"
        "换货时间窗口\n"
        "自购买之日起 30 天内，出现质量问题的产品可申请免费换货；超过 30 天窗口期的换货请求不予受理。\n"
        "\n"
        "换货资格\n"
        "换货需同时满足：产品仍在换货时间窗口内；问题属于质量问题而非人为损坏；订单状态为已妥投；仓库有可用库存。\n"
        "\n"
        "审批与风险依据\n"
        "订单金额超过 500 元的换货申请需人工审批后方可执行；金额不超过 500 元且满足全部资格条件的换货可直接执行。\n"
    ),
)
