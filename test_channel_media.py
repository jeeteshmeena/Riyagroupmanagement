#!/usr/bin/env python3
import os
import sys

sys.modules['OpenSSL'] = None
sys.modules['OpenSSL.crypto'] = None
sys.modules['OpenSSL.SSL'] = None

import pymongo
import mongo_search

def inspect():
    col, client, settings = mongo_search._get_mongo_client()
    if not col:
        print("❌ Cannot connect to Mongo")
        return

    print("="*70)
    print(f"📊 FULL 298-STORY IMAGE STORAGE BREAKDOWN")
    print(f"Database: {col.database.name} | Collection: {col.name}")
    print("="*70)

    total_docs = col.count_documents({})
    r2_count = 0
    api_count = 0
    file_id_count = 0
    no_image_count = 0
    distinct_domains = set()

    for doc in col.find({}):
        poster_url = str(doc.get("poster_url") or "").strip()
        image = str(doc.get("image") or "").strip()
        
        if poster_url.startswith("http://") or poster_url.startswith("https://"):
            r2_count += 1
            domain = poster_url.split("/")[2] if len(poster_url.split("/")) > 2 else poster_url
            distinct_domains.add(domain)
        elif poster_url.startswith("/api/"):
            api_count += 1
        elif image.startswith("AgAC"):
            file_id_count += 1
        else:
            no_image_count += 1

    print(f"Total Stories: {total_docs}")
    print(f"  • Full Cloudflare R2 / HTTP URLs: {r2_count}")
    print(f"  • /api/tg-image URLs:             {api_count}")
    print(f"  • Raw Telegram file_ids (AgAC...): {file_id_count}")
    print(f"  • No image fields at all:         {no_image_count}")
    print(f"\nDistinct HTTP Domains Found: {distinct_domains}")

    print("\nSample Stories with each format:")
    # 1 sample R2
    s_r2 = col.find_one({"poster_url": {"$regex": "^https?://"}})
    if s_r2:
        print(f"  [R2] {s_r2.get('story_name_en')}: poster_url='{s_r2.get('poster_url')}'")

    # 1 sample API
    s_api = col.find_one({"poster_url": {"$regex": "^/api/"}})
    if s_api:
        print(f"  [API] {s_api.get('story_name_en')}: poster_url='{s_api.get('poster_url')}'")

    # 1 sample file_id
    s_fid = col.find_one({"image": {"$regex": "^AgAC"}, "poster_url": {"$exists": False}})
    if s_fid:
        print(f"  [FileID] {s_fid.get('story_name_en')}: image='{s_fid.get('image')[:40]}...'")

    client.close()
    print("="*70)

if __name__ == "__main__":
    inspect()
