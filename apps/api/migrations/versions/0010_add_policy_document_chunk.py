"""add policy document and chunk persistence

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # policy_documents 是 T024 "政策知识文档"的权威落点：保存 stable business key、
    # title、source reference、version/content identity、raw canonical content、
    # demo flag 与 ingested timestamp。现有 domain 中没有任何表能承载"一份可摄取、
    # 可追溯来源、可重复摄取的知识文档"，因此本迁移确属必需。
    #
    # business_key 与 source_reference 两个唯一约束共同构成"同一份政策文档"的身份，
    # 是幂等摄取与变更检测的持久化依据；content_identity 是 canonical content 的
    # sha256，version 在 content 变化时递增。
    op.create_table(
        "policy_documents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("business_key", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("source_reference", sa.String(length=256), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_identity", sa.String(length=64), nullable=False),
        sa.Column("raw_content", sa.Text(), nullable=False),
        sa.Column("is_demo_data", sa.Boolean(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_key"),
        sa.UniqueConstraint("source_reference"),
    )
    op.create_index(
        op.f("ix_policy_documents_business_key"),
        "policy_documents",
        ["business_key"],
        unique=True,
    )
    op.create_index(
        op.f("ix_policy_documents_source_reference"),
        "policy_documents",
        ["source_reference"],
        unique=True,
    )

    # policy_chunks 保存 document 的确定性分块。chunk_order 从 1 开始稳定递增；
    # (document_id, chunk_order) 唯一保证同一文档内 chunk 顺序确定；business_key
    # 唯一提供跨重复摄取的稳定 chunk identity；source_reference 冗余保存来源定位符，
    # 使 chunk 即使不 join document 也保留 source identity。
    #
    # document_id 使用 ON DELETE CASCADE，使 demo 重置（clear_demo_data 删除
    # documents）保持可重复。
    op.create_table(
        "policy_chunks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("business_key", sa.String(length=128), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("chunk_order", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source_reference", sa.String(length=256), nullable=False),
        sa.Column("is_demo_data", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["policy_documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_key"),
        sa.UniqueConstraint(
            "document_id", "chunk_order", name="uq_policy_chunks_document_order"
        ),
    )
    op.create_index(
        op.f("ix_policy_chunks_business_key"),
        "policy_chunks",
        ["business_key"],
        unique=True,
    )
    op.create_index(
        op.f("ix_policy_chunks_document_id"),
        "policy_chunks",
        ["document_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_policy_chunks_document_id"), table_name="policy_chunks")
    op.drop_index(op.f("ix_policy_chunks_business_key"), table_name="policy_chunks")
    op.drop_table("policy_chunks")
    op.drop_index(op.f("ix_policy_documents_source_reference"), table_name="policy_documents")
    op.drop_index(op.f("ix_policy_documents_business_key"), table_name="policy_documents")
    op.drop_table("policy_documents")
