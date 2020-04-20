# This script MUST be called with python 2.x
#!/usr/bin/env python

import json
import re
import os
import datetime
import argparse
import requests
import sys

VERBOSE      = False
TEST_MODE    = True
QUICK_TEST   = False
MAX_DAYS     = 30
BASE_PATH    = 'http://artifactory.bullhorn.com:8081/artifactory/api/storage'
DEV_PATH     = BASE_PATH + '/npm-dev'
DEV_CATALOG  = "dev_catalog.txt"
REL_PATH     = BASE_PATH + '/npm-release'
REL_CATALOG  = "release_catalog.txt"
KEEP_FILES   = 'keepers.txt'
DELETE_FILES = 'deleters.txt'

tmp             = datetime.datetime.today()
todays_date_str = tmp.strftime("%Y-%m-%d")
todays_date     = tmp.strptime(todays_date_str, "%Y-%m-%d")

#USER = 'jenkins_publisher'
#PASS = 'artifactory4bullhorn'

def collect_data(uri):
    """Collect URI data via curl and return as JSON dict"""

    tmp_file = './.tempfile.txt'

    data = list()
    curl_str = 'curl ' + uri + " -o " + tmp_file + " > /dev/null 2>&1"
    if VERBOSE: print 'Processing: %s' % uri
    os.system(curl_str)
    try:
        with open(tmp_file) as file_in:
            data = json.load(file_in)
    except IOError:
        print 'Could not read "%s"' % tmp_file
        sys.exit()

    return data

def user_input(msg):
    """Display 'msg' and wait for user to press enter"""
    print msg
    if VERBOSE: raw_input('Press Enter to continue')

# Traverse through folders. Will call itself to move deeper down the tree
def traverse(repo_name, data, catalog):
    if repo_name == 'dev':
        use_repo = DEV_PATH
    elif repo_name == 'rel':
        use_repo = REL_PATH
    else:
        if VERBOSE: print 'Invalid repo name (%s)' % repo_name
        return catalog

    for c in data['children']:  # Follow the children folders
        new_path = use_repo + data['path'] + c['uri']   # Generate a new parent (BASE + child folder)
        new_data = collect_data(new_path)    # Run the curl on the new path
        if c['folder']:         # If the child is a folder, traverse it
            traverse(repo_name, new_data, catalog)                                      # Traverse the new path
        else:                                                       # If not a folder then save the file and date
            # This is a file but we still need the file perms on it
            file = data['path'] + c['uri']                          # File (after DEV_PATH start point)
            catalog[file] = new_data['created']

    return(catalog)

def read_data(file):
    """Read the outut of a real run. Each line is a K|V pair to repopulate the dicts"""
    data = list()
    dct  = dict()

    if not os.path.exists(file):
        if VERBOSE: print '%s not found!' % file
        sys.exit(1)

    # Read a saved file and store in a list
    with open(file) as fi:
        for line in fi:
            data.append(line)

    # Process the list into dictionary key value pairs and populate a new dict (to return)
    for x in data:
        x = x.strip()

        # There should only be two values (key|value). If there are more or less that's an issue, so skip it
        if len(x.split('|')) != 2:
            print '\"%s\" does not have two fields (%d) .. skipping' % (x, len(x.split('|')))
            continue

        (k, v) = x.split("|")
        dct[k] = v

    return(dct) # Return the new dictionary

def show_catalog(cat):
    print '\nDisplay catalog, %d entries..' % len(cat)
    user_input('Press Enter when ready to view')
    for k in sorted(cat):
        print '%s :created on: %s' % (k, cat[k])

def save_catalog(dct, file):
    if VERBOSE: print 'Writing %s' % file
    with open(file, 'w') as fo:
        for k in sorted(dct):
            fo.write('%s|%s\n' % (k, dct[k]))

def write_list(file, lst):
    """Write the list to file"""

    if VERBOSE: print 'Writing list to %s' % file
    with open(file, 'w') as file_ptr:
        for k in sorted(lst):
            file_ptr.write('%s\n' % k)

def delete_files(lst, u, p):
    global VERBOSE

    for f in lst:
        file = DEV_PATH + f
        if VERBOSE: print 'deleting "%s"' % file

        if TEST_MODE:
            resp = requests.get(file, auth=(u, p))
            print resp
            user_input('Next')
        else:
            pass    # Remove this in real life

        # https://en.wikipedia.org/wiki/List_of_HTTP_status_codes
            resp = requests.delete(f, auth=(u, p))
            if not re.match(r'<Response [2\d\d]>', resp):   # If we see a non-success message, print it
                print resp

def main():
    global MAX_DAYS, QUICK_TEST, VERBOSE

    rel_catalog = dict()
    dev_catalog = dict()
    keep   = list()
    delete = list()

    parser = argparse.ArgumentParser(description='NPM artifact cleaner')
    parser.add_argument('-d', '--days', help='Remove files older than this value', type=int)
    parser.add_argument('-q', '--quick', help='Quick test (Done create dlete_list file', action='store_true')
    parser.add_argument('-v', '--verbose', help='Be verbose in processing', action='store_true')
    if not TEST_MODE:
        parser.add_argument('-u', '--user', help='username', required=True, type=str)
        parser.add_argument('-p', '--password', help='passwd', required=True, type=str)
    else:
        user = 'jenkins_publisher'
        passwd = 'artifactory4bullhorn'

    args = parser.parse_args()

    if args.days:
        MAX_DAYS = args.days

    if args.quick:
        QUICK_TEST = True

    if args.verbose:
        VERBOSE = True

    if not TEST_MODE:
        user = args.user
        passwd = args.password

    # Instead of continuously polling artifactory, I have the data saved and will just read it
    if TEST_MODE:
        if VERBOSE: print 'Running in TEST_MODE'
        dev_catalog = read_data(DEV_CATALOG)
        if VERBOSE: print '%d files read from %s' % (len(dev_catalog), DEV_CATALOG)
#        show_catalog(dev_catalog)
        rel_catalog = read_data(REL_CATALOG)
        if VERBOSE: print '%d files read from %s' % (len(rel_catalog), REL_CATALOG)
        user_input('Data is loaded in catalogs')
    else:

        print '\nGenerating npm-dev catalog'
        dev_base = collect_data(DEV_PATH)
        traverse('dev', dev_base, dev_catalog)
    #    show_catalog(dev_catalog)
        save_catalog(dev_catalog, DEV_CATALOG)

        print '\nGenerating npm-release catalog'
        rel_base = collect_data(REL_PATH)
        traverse('rel', rel_base, rel_catalog)
    #    show_catalog(rel_catalog)
        save_catalog(rel_catalog, REL_CATALOG)

    # Now that I have all the development and release files, with their creation dates, it's time to process them
    # Files > MAX_DAYS and are NOT in the release catalog can be deleted

    for dev_file in sorted(dev_catalog):    # Loop through the development files
        if VERBOSE: print 'processing: %s' % dev_file
        if not dev_file in rel_catalog:     # If dev file is NOT in the release catalog, check date, etc
            if VERBOSE: print '  is not in rel_catalog',
            tmp           = re.search(r'(.*)(-\d{2,}:\d{2,})', dev_catalog[dev_file])    # Strip off timezone
            tmp_time      = tmp.groups()[0]                                              # Save string w/o TZ
            file_dt       = datetime.datetime.strptime(tmp_time, '%Y-%m-%dT%H:%M:%S.%f') # Convert dev_file to datetime obj
            file_date_str = datetime.datetime.strftime(file_dt, '%Y-%m-%d')              # Create a 'date' (only) string
            file_date     = datetime.datetime.strptime(file_date_str, '%Y-%m-%d')        # Create a 'date' (only) object
            delta         = todays_date - file_date
            if VERBOSE: print 'and is %d days old' % delta.days,

            if delta.days > MAX_DAYS:
                if VERBOSE: print '( > %d days old) .. mark for removal' % MAX_DAYS
#                print '( > %d days old) .. continue checking parameters for removal' % MAX_DAYS
                delete.append(dev_file)         # Put this file in the delete list
            else:
                if VERBOSE: print '( <= %d days old) .. keeping it' % MAX_DAYS
                keep.append(dev_file)           # Put this file in the keep list
        else:
            if VERBOSE: print '  is listed in release catalog and will be kept'
            keep.append(dev_file)           # Put this file in the keep list

    write_list(KEEP_FILES, keep)
    write_list(DELETE_FILES, delete)

    delete_files(delete, user, passwd)

if __name__ == '__main__':
    main()
