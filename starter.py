# encoding: utf-8

import os
import ssl
import sys
import csv
import json
import time
import urllib
import urllib2
import base64
import StringIO

from datetime import datetime
from datetime import timedelta

try:
    import pyodbc
except ImportError:
    pass

reload(sys)  
sys.setdefaultencoding('utf8')

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

global _debug
_debug = True


def _post(url, query, options):

    request = urllib2.Request(url, urllib.urlencode({
        "query": query,
        "header": "yes"
    }))
    base64string = base64.b64encode('%s:%s' % (options['username'], options['password']))
    request.add_header("Authorization", "Basic %s" % base64string)
    request.get_method = lambda: 'POST'
    r = urllib2.urlopen(request, context=ctx)
    body = r.read()
    r.close()

    if _debug:
        msg = 'Status code: %s' % str(r.code)

        print '\n\t----------- POST FUNCTION -----------'
        print '\t' + url
        print '\t' + msg
        print '\tQuery: ' + query
        print '\t------- END OF POST FUNCTION -------\n'

    return body


def get_list_from_csv(text):
    f = StringIO.StringIO(text)
    list_ = []
    dict_reader = csv.DictReader(f, quotechar='"', delimiter=',', quoting=csv.QUOTE_ALL, skipinitialspace=True, dialect='excel')
    for item in dict_reader:
        list_.append(item)

    return list_, [x for x in dict_reader.fieldnames]


def doql_call(config, query):

    limit = 0
    query['query'] = ' '.join(query['query'].split()).lower()

    # prepare date-filtered query
    if query['date'] and query['date']['column'] and query['date']['days_limit']:
        index = None
        where_index = query['query'].find('where')
        order_index =  query['query'].find('order by')

        if where_index > 0:
            index = where_index + 6
            query['query'] = query['query'][:index] + " %s > current_date - interval '%s day' and " % (query['date']['column'], query['date']['days_limit']) + query['query'][index:]
        elif order_index > 0:
            index = order_index
            query['query'] = query['query'][:index] + " where %s > current_date - interval '%s day' " % (query['date']['column'], query['date']['days_limit']) + query['query'][index:]

    if query['output_format'] == 'csv' or query['output_format'] == 'json':
        if query['offset']:
            page = 0
            _next = True
            while _next:

                doql_offset = page * query['offset']
                doql_limit = query['offset']

                if query['limit'] and query['limit'] > query['offset']:
                    if (doql_offset + query['offset']) > query['limit']:
                        doql_limit = query['limit'] - doql_offset
                else:
                    if query['limit']:
                        doql_limit = query['limit']

                doql_query = query['query'] + ' LIMIT %s OFFSET %s' % (doql_limit, doql_offset)

                res = _post(
                    'https://%s/services/data/v1.0/query/' % config['host'], doql_query, {
                        'username': config['username'],
                        'password': config['password']
                    }
                )
                lines = res.split('\n')

                if query['output_format'] == 'csv':
                    file = open('%s_%s_%s.csv' % (query['output_filename'], time.strftime("%Y%m%d%H%M%S"), page), 'w+')
                    file.write('\n'.join(lines))
                elif query['output_format'] == 'json':
                    csv_list, field_order = get_list_from_csv('\n'.join(lines))
                    file = open('%s_%s_%s.json' % (query['output_filename'], time.strftime("%Y%m%d%H%M%S"), page), 'w+')
                    file.write(json.dumps(csv_list, indent=4, sort_keys=True))

                if doql_limit != query['offset'] or (len(lines) - 2) != query['offset'] or (doql_offset + doql_limit) == query['limit'] :
                    break

                page += 1

        else:

            if query['limit']:
                doql_query = query['query'] + ' LIMIT %s ' % query['limit']
            else:
                doql_query = query['query']

            res = _post(
                'https://%s/services/data/v1.0/query/' % config['host'], doql_query, {
                    'username': config['username'],
                    'password': config['password']
                }
            )
            lines = res.split('\n')

            if query['output_format'] == 'csv':
                file = open('%s_%s.csv' % (query['output_filename'], time.strftime("%Y%m%d%H%M%S")), 'w+')
                file.write(res)
            elif query['output_format'] == 'json':
                csv_list, field_order = get_list_from_csv('\n'.join(lines))
                file = open('%s_%s.json' % (query['output_filename'], time.strftime("%Y%m%d%H%M%S")), 'w+')
                file.write(json.dumps(csv_list, indent=4, sort_keys=True))

        file.close()

    elif query['output_format'] == 'database':

        if query['limit']:
            doql_query = query['query'] + ' LIMIT %s ' % query['limit']
        else:
            doql_query = query['query']

        res = _post(
            'https://%s/services/data/v1.0/query/' % config['host'], doql_query, {
                'username': config['username'],
                'password': config['password']
            }
        )
        lines = res.split('\n')
        header = lines.pop(0)

        cnxn = pyodbc.connect(query['connection_string'], autocommit=True)
        conn = cnxn.cursor()

        for line in lines:
            # some special cases for strange DOQL responses ( that may break database such as MySQL )
            if len(line) > 0:
                if '",' in line:
                    line = line.replace(',', '__comma__')
                    line = line.replace('"__comma__', '",')
                if ',"' in line:
                    line = line.replace(',', '__comma__')
                    line = line.replace('__comma__"', ',"')
                if '","' in line:
                    line = line.replace(',', '__comma__')
                    line = line.replace('"__comma__"', '","')

                line = line.replace('\,', ',')
                if line.endswith('\\'):
                    line = line[:-1]
                query_str = "INSERT INTO %s (%s) VALUES (%s)" % (query['table'], header, ",".join(["'%s'" % x.replace('__comma__', ',').replace("'", "\\'") for x in line.split(',')]))
                conn.execute(query_str)

        print("Added %s records" % len(lines))

        conn.close()


def main():
    try:
        with open('settings.json') as data_file:
            config = json.load(data_file)
    except IOError:
        print 'File "settings.json" doesn\'t exists.'
        sys.exit()

    try:
        with open(sys.argv[1]) as data_file:
            query = json.loads(data_file.read().replace('\n', '').replace("  ", ' '))
    except IOError:
        print 'File "%s" doesn\'t exists.' % sys.argv[1]
        sys.exit()
    doql_call(config, query)

if __name__ == "__main__":

    if len(sys.argv) < 2:
        print 'Please use "python starter.py query.json".'
        sys.exit()

    main()
    print 'Done!'
    sys.exit()
