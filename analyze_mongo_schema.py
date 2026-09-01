#!/usr/bin/env python3
import os
import json
import logging
from typing import Dict, Any, List

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('MongoAnalyzer')

import sys
# Bypass broken PyOpenSSL in Ubuntu to force standard library ssl
sys.modules['OpenSSL'] = None
sys.modules['OpenSSL.crypto'] = None
sys.modules['OpenSSL.SSL'] = None

try:
    import pymongo
except ImportError:
    logger.error('pymongo is not installed! Run: pip install pymongo')
    pymongo = None

def get_settings():
    settings = {}
    settings_file = 'poster_settings.json'
    if os.path.exists(settings_file):
        try:
            with open(settings_file, 'r', encoding='utf-8') as f:
                settings = json.load(f)
        except Exception as e:
            logger.warning(f'Failed to read poster_settings.json: {e}')
    
    mongo_uri = settings.get('mongo_uri') or os.getenv('MONGO_URI', '')
    db_name = settings.get('database_name') or os.getenv('DATABASE_NAME', 'arya_premium')
    col_name = settings.get('collection_name') or 'premium_stories'
    return mongo_uri, db_name, col_name

def analyze():
    if not pymongo:
        return
    
    mongo_uri, db_name, col_name = get_settings()
    if not mongo_uri:
        print('\n❌ No MONGO_URI found in poster_settings.json or environment variables.')
        print('Please set MONGO_URI in poster_settings.json or .env file.\n')
        return

    print('='*70)
    print('🔍 MONGO DB READ-ONLY SCHEMA & IMAGE STORAGE ANALYSIS')
    print('='*70)
    print(f'Target Database: {db_name}')
    print(f'Default Collection: {col_name}')
    print('='*70)

    try:
        client = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=6000)
        db = client[db_name]
        
        # 1. List all collections in DB
        collections = db.list_collection_names()
        print(f'\n📂 Total Collections Found: {len(collections)}')
        for c in collections:
            cnt = db[c].count_documents({})
            print(f'   📁 {c} ({cnt} documents)')
        
        # 2. Deep inspect target collection & other story collections
        target_cols = [c for c in collections if any(k in c.lower() for k in ['story', 'stories', 'premium', 'post', 'book'])]
        if col_name in collections and col_name not in target_cols:
            target_cols.insert(0, col_name)
        if not target_cols:
            target_cols = collections[:3]

        for target_c in target_cols:
            print('\n' + '-'*70)
            print(f'🔬 Analyzing Collection: {target_c}')
            print('-'*70)
            
            c_handle = db[target_c]
            total_docs = c_handle.count_documents({})
            print(f'Document count: {total_docs}')
            if total_docs == 0:
                continue

            # Fetch sample documents
            sample_docs = list(c_handle.find({}).limit(25))
            
            all_keys = set()
            image_keys = set()
            r2_urls = []
            http_urls = []
            file_ids = []
            
            for doc in sample_docs:
                for k, v in doc.items():
                    all_keys.add(k)
                    k_lower = k.lower()
                    if any(img_w in k_lower for img_w in ['poster', 'cover', 'image', 'photo', 'thumb', 'media', 'banner', 'pic', 'r2', 'cloudflare', 'file']):
                        image_keys.add(k)
                        
                        def check_val(val, path_str):
                            if isinstance(val, str):
                                val_s = val.strip()
                                if 'r2.dev' in val_s or 'r2.cloudflarestorage' in val_s or 'cloudflare' in val_s:
                                    r2_urls.append((path_str, val_s))
                                elif val_s.startswith(('http://', 'https://')):
                                    http_urls.append((path_str, val_s))
                                elif len(val_s) >= 20 and ' ' not in val_s:
                                    file_ids.append((path_str, val_s))
                            elif isinstance(val, dict):
                                for sub_k, sub_v in val.items():
                                    check_val(sub_v, f'{path_str}.{sub_k}')
                            elif isinstance(val, list):
                                for idx, item in enumerate(val[:3]):
                                    check_val(item, f'{path_str}[{idx}]')

                        check_val(v, k)

            print('\n📋 All Document Fields in Sample:')
            print('  ', ', '.join(sorted(all_keys)))

            print('\n🖼️ Image/Media-Related Fields Found:')
            print('  ', ', '.join(sorted(image_keys)) if image_keys else 'None detected')

            print('\n☁️ Cloudflare R2 / CDN Image URLs Detected in Sample:')
            if r2_urls:
                for p, u in r2_urls[:5]:
                    print(f'   • [{p}] -> {u}')
            else:
                print('   (No explicit r2.dev domain string in first 25 samples)')

            print('\n🌐 General HTTP/HTTPS Image URLs Detected:')
            if http_urls:
                for p, u in http_urls[:5]:
                    print(f'   • [{p}] -> {u}')
            else:
                print('   (None detected in sample)')

            print('\n🆔 Telegram File IDs / Hashes Detected:')
            if file_ids:
                for p, f in file_ids[:5]:
                    print(f'   • [{p}] -> {f[:30]}...')
            else:
                print('   (None detected in sample)')

            # Print 1 full structured sample document
            if sample_docs:
                first_doc = sample_docs[0].copy()
                first_doc['_id'] = str(first_doc.get('_id'))
                print('\n📄 Sample Document Structure (First Document):')
                print(json.dumps(first_doc, indent=2, ensure_ascii=False, default=str))

        client.close()
        print('\n' + '='*70)
        print('✅ Analysis completed successfully (100% Read-Only Mode).')
        print('='*70 + '\n')

    except Exception as e:
        logger.error(f'MongoDB Analysis Error: {e}')

if __name__ == '__main__':
    analyze()
