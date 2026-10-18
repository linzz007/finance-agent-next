"""Public API for the optional finance domain; no direct database seeding."""

from typing import Any
from pydantic import BaseModel, Field

from policy_impact.domains.finance.profile import load_profile, save_profile, ProfileSyncError
from policy_impact.domains.finance.store import FinanceConflictError, FinanceStore


class FinanceProfileRequest(BaseModel):
    profile: dict[str, Any]
    expected_version: int = Field(ge=0)


class FinanceScheduleRequest(BaseModel):
    enabled: bool


class FinanceArticleCreateRequest(BaseModel):
    author: str = Field(min_length=1, max_length=60)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=20, max_length=200_000)
    published_at: str = Field(min_length=10, max_length=10)
    source_url: str = ""


def register_finance_routes(app, http_error):
    def perform(fn):
        try:
            return fn()
        except FileNotFoundError as exc:
            raise http_error(status_code=404, detail=str(exc)) from exc
        except FinanceConflictError as exc:
            raise http_error(status_code=409, detail=str(exc)) from exc
        except ProfileSyncError as exc:
            raise http_error(status_code=409, detail={"message": str(exc), "committed_version": exc.committed_version}) from exc
        except ValueError as exc:
            raise http_error(status_code=400, detail=str(exc)) from exc

    def require_profile(company_id):
        if not load_profile(company_id):
            raise ValueError("当前工作空间没有金融档案")

    def article_corpus(company_id):
        if company_id != "finance_observer":
            raise ValueError("文章资料库仅在金融研判工作空间开放")
        from policy_impact.domains.finance.article_corpus import ArticleCorpus
        corpus = ArticleCorpus(company_id)
        return corpus, corpus.sync()

    @app.get("/companies/{company_id}/workbench/finance/articles")
    def list_articles(company_id: str):
        def run():
            corpus, sync = article_corpus(company_id)
            # Metadata is bounded and body text remains an explicit read
            # operation, so the UI can enumerate the complete corpus without
            # placing article contents into one response or model context.
            articles = corpus.list_articles(limit=2000)
            return {"articles": articles, "authors": sorted({item["author"] for item in articles}),
                    "sync": sync}
        return perform(run)

    @app.get("/companies/{company_id}/workbench/finance/articles/{article_id}")
    def read_article(company_id: str, article_id: str):
        def run():
            corpus, _ = article_corpus(company_id)
            article = corpus.get_article(article_id)
            if article is None:
                raise FileNotFoundError("未找到这篇文章")
            return {"article": article}
        return perform(run)

    @app.post("/companies/{company_id}/workbench/finance/articles", status_code=201)
    def create_article(company_id: str, payload: FinanceArticleCreateRequest):
        def run():
            corpus, _ = article_corpus(company_id)
            article = corpus.add_article(**payload.model_dump())
            return {"article": article}
        return perform(run)

    @app.get("/companies/{company_id}/workbench/finance/profile")
    def get_profile(company_id: str):
        return perform(lambda: {"profile": load_profile(company_id)})

    @app.post("/companies/{company_id}/workbench/finance/profile")
    def put_profile(company_id: str, payload: FinanceProfileRequest):
        return perform(lambda: {"profile": save_profile(company_id, payload.profile, expected_version=payload.expected_version)})

    @app.get("/companies/{company_id}/workbench/finance/status")
    def get_status(company_id: str):
        from policy_impact.domains.finance.scheduler import get_collection_status
        def run():
            require_profile(company_id)
            return {"status": get_collection_status(company_id)}
        return perform(run)

    @app.post("/companies/{company_id}/workbench/finance/schedule")
    def set_schedule(company_id: str, payload: FinanceScheduleRequest):
        from policy_impact.domains.finance.scheduler import set_collection_enabled
        def run():
            require_profile(company_id)
            return {"status": set_collection_enabled(company_id, payload.enabled)}
        return perform(run)

    @app.post("/companies/{company_id}/workbench/finance/collect")
    def collect(company_id: str):
        from policy_impact.domains.finance.scheduler import collect_once
        def run():
            require_profile(company_id)
            return collect_once(company_id)
        return perform(run)

    @app.get("/companies/{company_id}/workbench/finance/reports")
    def reports(company_id: str, limit: int = 20):
        def run():
            require_profile(company_id)
            records = FinanceStore(company_id).list_reports(limit=1000)
            return {"reports": [r for r in records if r.get("delivery_mode") != "chat_analysis"][:max(1, min(50, limit))]}
        return perform(run)

    @app.get("/companies/{company_id}/workbench/finance/reports/{report_id}")
    def report(company_id: str, report_id: str):
        def run():
            require_profile(company_id)
            value = FinanceStore(company_id).get_report(report_id)
            if value is None:
                raise FileNotFoundError("当前工作空间未找到该报告")
            return {"report": value}
        return perform(run)
