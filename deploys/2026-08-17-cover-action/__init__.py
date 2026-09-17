"""
Database Models Package
Import order matters for SQLAlchemy relationships!
"""
from .user import User
from .production_order import ProductionOrder
from .chemical import (
    Furnace, Machine, DefectType, DecisionType,
    ElementSpecification, Shift, Engineer, ChemicalAnalysis
)
from .pipe import Pipe, PipeStage
from .mechanical import MechanicalTest
from .stage_defect_type import StageDefectType
from .stage_decision_type import StageDecisionType
from .stage_history import PipeStageHistory
from .product import ProductParameter, Product, Customer, Mold
from .audit import AuditLog
from .nonconformance_action import NonConformanceAction
from .permission import Permission, RolePermission
from .attachment import Attachment
from .bundle import Bundle
from .chat import ChatSession, ChatMessage

__all__ = [
    'User',
    'ProductionOrder',
    'Furnace',
    'Machine',
    'DefectType',
    'DecisionType',
    'ElementSpecification',
    'Shift',
    'Engineer',
    'ChemicalAnalysis',
    'Pipe',
    'PipeStage',
    'MechanicalTest',
    'StageDefectType',
    'StageDecisionType',
    'PipeStageHistory',
    'ProductParameter',
    'Product',
    'Customer',
    'Mold',
    'AuditLog',
    'NonConformanceAction',
    'Permission',
    'RolePermission',
    'Attachment',
    'Bundle',
]
