from supabase import create_client, Client
from config import Config

# Initialize Supabase client
supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

class Database:
    """Database connection and operations"""
    
    @staticmethod
    def get_client():
        """Get Supabase client instance"""
        return supabase
    
    @staticmethod
    def get_table(table_name):
        """Get a table reference"""
        return supabase.table(table_name)
