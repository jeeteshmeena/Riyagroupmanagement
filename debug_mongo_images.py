#!/usr/bin/env python3
import os
import sys
import json
import logging

sys.modules['OpenSSL'] = None
sys.modules['OpenSSL.crypto'] = None
sys.modules['OpenSSL.SSL'] = None

import pymongo
import requests
from io import BytesIO
from PIL import Image

def test():
    import mongo_search
    queries = ['Hukum ka ikka', 'Wild card entry', 'Dhruvash', "Devil's little Girl", 'Hiss']
    
    col, client, settings = mongo_search._get_mongo_client()
    if col is None:
        print('❌ Could not connect to MongoDB!')
        return

    print('='*70)
    print('🔍 DEBUGGING MONGO STORIES IMAGE RESOLUTION')
    print('Database:', col.database.name, '| Collection:', col.name)
    print('='*70)

    for q in queries:
        print('\n👉 Testing Query:', repr(q))
        res = mongo_search.search_story_from_mongo_sync(q)
        if not res:
            print('   ❌ Story not found in MongoDB!')
            continue

        print('   ✅ Story Found:', repr(res.get('text')))
        img_url = res.get('photo') or res.get('image')
        print('   🖼️ Image URL in Result:', repr(img_url))
        
        # Raw document fields
        from bson import ObjectId
        doc_id = res['mongo_id']
        try:
            doc = col.find_one({'_id': ObjectId(doc_id)})
        except Exception:
            doc = col.find_one({'_id': doc_id})

        if doc:
            print('   📋 Raw Document Keys:', list(doc.keys()))
            for k in ['poster_url', 'poster', 'square_poster', 'image_url', 'cover', 'image', 'banner_url', 'banner']:
                if k in doc:
                    print('      • doc[' + repr(k) + '] =', repr(str(doc[k]))[:100])

        # Test Image Download
        if img_url and img_url.startswith(('http://', 'https://')):
            try:
                headers = {'User-Agent': 'Mozilla/5.0'}
                resp = requests.get(img_url, headers=headers, timeout=10)
                print('   🌐 Download Status:', resp.status_code, '| Bytes:', len(resp.content), '| Content-Type:', resp.headers.get('Content-Type'))
                if resp.status_code == 200 and len(resp.content) > 100:
                    im = Image.open(BytesIO(resp.content)).convert('RGB')
                    print('   📐 Pillow Image Dimensions:', im.size, '(W x H)')
                    
                    w, h = im.size
                    min_s = min(w, h)
                    left = (w - min_s) // 2
                    top = (h - min_s) // 2
                    im_cropped = im.crop((left, top, left + min_s, top + min_s))
                    buf = BytesIO()
                    im_cropped.save(buf, format='JPEG', quality=95)
                    buf.seek(0)
                    print('   ✨ Cropped 1:1 JPEG buffer size:', len(buf.getvalue()), 'bytes -> READY FOR TELEGRAM!')
                else:
                    print('   ⚠️ Download failed or returned empty content!')
            except Exception as dl_err:
                print('   ❌ Download Exception:', dl_err)
        else:
            print('   ⚠️ Image URL is not a valid HTTP/HTTPS URL!')

    client.close()
    print('\n' + '='*70)

if __name__ == '__main__':
    test()
