from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config.settings import settings
from app.storage.schedules import ScheduleRepository
from app.services.autonomous.decision import decision_provider_readiness
from app.services.local_autonomous import execution_arming_status, readiness_snapshot
from app.storage.execution import ExecutionRepository
from app.services.autonomous.position_management import PositionManagementService, feature_flags

router = APIRouter()

@router.get("/health")
def health():
    return {"status": "ok", **({"mode": settings.app_mode} if settings.is_local_autonomous else {})}

@router.get("/ready")
def ready():
    scheduler=ScheduleRepository().worker_health()
    if settings.is_local_autonomous:
        components=readiness_snapshot()
        controls=ExecutionRepository().get_autonomous_controls(settings.local_user_id)
        management_repo=ExecutionRepository();management=management_repo.management_status(settings.local_user_id)
        unresolved=len(management.get("unresolved_submissions",[]));latest=management.get("latest_cycle") or {}
        summary=latest.get("summary") or {};flags=feature_flags();wiring=PositionManagementService.wiring_status()
        effective_kill=settings.kill_switch_enabled or controls["global_autonomous_kill_switch"]
        components.update({
            "scheduler": {"ready": scheduler["status"] == "healthy", "status": scheduler["status"]},
            "scheduler_heartbeat": {"ready": scheduler["status"] == "healthy", "status": scheduler["status"]},
            "decision_provider": decision_provider_readiness(settings.autonomous_decision_provider,
                settings.autonomous_decision_model),
            "live_trading_flag": {"ready": settings.live_trading_enabled, "enabled": settings.live_trading_enabled},
            "autonomous_execution_flag": {"ready": settings.autonomous_execution_enabled,
                "enabled": settings.autonomous_execution_enabled},
            "kill_switch": {"ready": not effective_kill, "enabled": effective_kill},
            "arming": execution_arming_status(),
            "tradelocker_account_discovery": components.get("selected_tradelocker_account",{"ready":False,"status":"not_ready"}),
            "resolved_acc_num": {"ready":bool(settings.tradelocker_account_number),"status":"ready" if settings.tradelocker_account_number else "not_ready"},
            "position_monitoring": {"ready":bool(latest),"status":"ready" if latest else "not_ready"},
            "full_close_capability": {"ready":wiring["adapter_methods"]["close_position"] and flags["full_close"]},
            "partial_close_capability": {"ready":wiring["adapter_methods"]["reduce_position"] and flags["partial_close"]},
            "stop_modification": {"ready":wiring["adapter_methods"]["modify_position_protection"] and flags["stop_modification"]},
            "take_profit_modification": {"ready":wiring["adapter_methods"]["modify_position_protection"] and flags["take_profit_modification"]},
            "break_even_capability": {"ready":flags["break_even"]},
            "trailing_stop_capability": {"ready":flags["trailing_stop"],"enabled":flags["trailing_stop"]},
            "pending_order_cancellation": {"ready":wiring["adapter_methods"]["cancel_order"] and flags["cancel_pending"]},
            "ambiguous_exit_reconciliation": {"ready":wiring["ambiguous_reconciliation"]},
            "unresolved_management_submissions": {"ready":unresolved==0,"count":unresolved},
            "unprotected_positions": {"ready":int(summary.get("unprotected_positions") or 0)==0,
                "count":int(summary.get("unprotected_positions") or 0)},
        })
        required=[value.get("ready",False) for key,value in components.items()
                  if key not in {"live_trading_flag","autonomous_execution_flag","kill_switch","execution_permission","arming",
                    "position_monitoring","trailing_stop_capability","unresolved_management_submissions","unprotected_positions"}]
        if settings.autonomous_scheduler_required_for_readiness:
            required.append(scheduler["status"] == "healthy")
        ready=all(required)
        return JSONResponse(status_code=200 if ready else 503,
            content={"status":"ready" if ready else "not_ready","components":components})
    if not settings.autonomous_scheduler_required_for_readiness:
        return {"status":"ready"}
    ready=scheduler["status"]=="healthy"
    return JSONResponse(status_code=200 if ready else 503,content={"status":"ready" if ready else "not_ready","scheduler":scheduler["status"]})

@router.get("/scheduler/health")
def scheduler_health():
    health=ScheduleRepository().worker_health()
    return {"status":health["status"],"worker_count":len(health["workers"]),
        "healthy_workers":sum(1 for item in health["workers"] if item["healthy"])}
