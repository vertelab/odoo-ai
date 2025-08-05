from datetime import date, datetime
from typing import Annotated, List, Optional
from pydantic import BaseModel, BeforeValidator, field_validator

import logging

_logger = logging.getLogger(__name__)


class AIQuestBase(BaseModel):
    """Base model for AI Quest"""
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = True


class AIQuestItem(AIQuestBase):
    """Individual AI Quest item with full details"""
    id: int
    name: str
    description: Optional[str] = None
    is_active: bool = True
    created_date: Optional[str] = None
    modified_date: Optional[str] = None

    class Config:
        from_attributes = True


class AIQuestListRequest(BaseModel):
    """Request model for listing AI quests"""
    limit: Optional[int] = 10
    offset: Optional[int] = 0

    @field_validator('limit')
    @classmethod
    def validate_limit(cls, v):
        if v is not None and (v < 1 or v > 100):
            raise ValueError('Limit must be between 1 and 100')
        return v

    @field_validator('offset')
    @classmethod
    def validate_offset(cls, v):
        if v is not None and v < 0:
            raise ValueError('Offset must be non-negative')
        return v


class AIQuestListResponse(BaseModel):
    """Response model for listing AI quests"""
    success: bool
    message: str
    quests: List[AIQuestItem] = []
    total_count: int = 0
    limit: int = 10
    offset: int = 0

    class Config:
        from_attributes = True


# AI Tool Models
class AIToolBase(BaseModel):
    """Base model for AI Tool"""
    name: Optional[str] = None
    is_active: Optional[bool] = True


class AIToolItem(AIToolBase):
    """Individual AI Tool item with full details"""
    id: int
    name: str
    is_active: bool = True
    created_date: Optional[str] = None
    modified_date: Optional[str] = None

    class Config:
        from_attributes = True


class AIToolListRequest(BaseModel):
    """Request model for listing AI tools"""
    limit: Optional[int] = 10
    offset: Optional[int] = 0

    @field_validator('limit')
    @classmethod
    def validate_limit(cls, v):
        if v is not None and (v < 1 or v > 100):
            raise ValueError('Limit must be between 1 and 100')
        return v

    @field_validator('offset')
    @classmethod
    def validate_offset(cls, v):
        if v is not None and v < 0:
            raise ValueError('Offset must be non-negative')
        return v


class AIToolListResponse(BaseModel):
    """Response model for listing AI tools"""
    success: bool
    message: str
    tools: List[AIToolItem] = []
    total_count: int = 0
    limit: int = 10
    offset: int = 0

    class Config:
        from_attributes = True