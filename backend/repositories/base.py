"""
Base Repository - Common MongoDB Operations
============================================
Provides base CRUD operations for all repositories.
"""
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorCollection
import logging

logger = logging.getLogger(__name__)


class BaseRepository:
    """Base repository with common MongoDB operations"""
    
    def __init__(self, collection: AsyncIOMotorCollection):
        self.collection = collection
        self._cache: Dict[str, Any] = {}
    
    async def find_one(self, query: Dict, projection: Dict = None) -> Optional[Dict]:
        """Find single document"""
        if projection is None:
            projection = {"_id": 0}
        return await self.collection.find_one(query, projection)
    
    async def find_many(
        self, 
        query: Dict = None, 
        projection: Dict = None,
        sort: List = None,
        limit: int = None
    ) -> List[Dict]:
        """Find multiple documents"""
        if query is None:
            query = {}
        if projection is None:
            projection = {"_id": 0}
        
        cursor = self.collection.find(query, projection)
        
        if sort:
            cursor = cursor.sort(sort)
        if limit:
            cursor = cursor.limit(limit)
        
        return await cursor.to_list(length=limit or 1000)
    
    async def insert_one(self, document: Dict) -> str:
        """Insert single document"""
        document["created_at"] = datetime.now(timezone.utc)
        result = await self.collection.insert_one(document)
        return str(result.inserted_id)
    
    async def insert_many(self, documents: List[Dict]) -> int:
        """Insert multiple documents"""
        if not documents:
            return 0
        
        now = datetime.now(timezone.utc)
        for doc in documents:
            doc["created_at"] = now
        
        result = await self.collection.insert_many(documents)
        return len(result.inserted_ids)
    
    async def update_one(self, query: Dict, update: Dict, upsert: bool = False) -> bool:
        """Update single document"""
        update["$set"] = update.get("$set", {})
        update["$set"]["updated_at"] = datetime.now(timezone.utc)
        
        result = await self.collection.update_one(query, update, upsert=upsert)
        return result.modified_count > 0 or result.upserted_id is not None
    
    async def update_many(self, query: Dict, update: Dict) -> int:
        """Update multiple documents"""
        update["$set"] = update.get("$set", {})
        update["$set"]["updated_at"] = datetime.now(timezone.utc)
        
        result = await self.collection.update_many(query, update)
        return result.modified_count
    
    async def delete_one(self, query: Dict) -> bool:
        """Delete single document"""
        result = await self.collection.delete_one(query)
        return result.deleted_count > 0
    
    async def delete_many(self, query: Dict = None) -> int:
        """Delete multiple documents"""
        if query is None:
            query = {}
        result = await self.collection.delete_many(query)
        return result.deleted_count
    
    async def count(self, query: Dict = None) -> int:
        """Count documents"""
        if query is None:
            query = {}
        return await self.collection.count_documents(query)
    
    async def distinct(self, field: str, query: Dict = None) -> List:
        """Get distinct values for a field"""
        if query is None:
            query = {}
        return await self.collection.distinct(field, query)
    
    async def aggregate(self, pipeline: List[Dict]) -> List[Dict]:
        """Run aggregation pipeline"""
        cursor = self.collection.aggregate(pipeline)
        return await cursor.to_list(length=1000)
    
    async def create_index(self, keys, **kwargs):
        """Create index on collection"""
        return await self.collection.create_index(keys, **kwargs)
    
    async def drop_indexes(self):
        """Drop all indexes except _id"""
        return await self.collection.drop_indexes()
    
    def clear_cache(self):
        """Clear in-memory cache"""
        self._cache = {}
