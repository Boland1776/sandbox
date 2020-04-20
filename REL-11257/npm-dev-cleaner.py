# This script MUST be called with python 2.x
#!/usr/bin/env python

# Called by Jenkins pipeline (from Hydrogen : Release_Engineering/Practice/Artifactory-NPM-cleanup)

import json
import re
import os
import datetime
import argparse
import requests
import sys
import time

# When set, we collect all the data via curl and save the data for future test runs. If the flag is False, we dont
# run the "curl" and rely on the data saved in the files (initially, these curls are taking 10's of minutes).
GEN_SAVED_DATA = True   # By default we poll and save the data (False when debugging and we catalog files saved)
VERBOSE      = False
TEST_MODE    = False    # Do not set when calling from pipeline
WAIT         = False    # Wait for user if true.
DO_DELETE    = False    # Saftey measure. You MUST call script with "-D" to actually delete
MAX_DAYS     = 30

# This is what we process
BASE_PATH    = 'http://artifactory.bullhorn.com:8081/artifactory/api/storage'
DEV_PATH     = BASE_PATH + '/npm-dev'
REL_PATH     = BASE_PATH + '/npm-release'

DEV_CATALOG  = "dev_catalog.txt"
REL_CATALOG  = "release_catalog.txt"
KEEP_FILES   = 'keepers.txt'
DELETE_FILES = 'deleters.txt'

# Skip the following folders in the npm-dev repo
SKIP_LIST    = ['.npm/@bullhorn-internal',
                '.npm/@bullhorn',
                '.npm/bh-elements',
                '.npm/chomsky',
                '.npm/galaxy-parser',
                '.npm-generator-novo'
                '@bullhorn-internal',
                '@bullhorn',
                'bh-elements',
                'chomsky',
                'galaxy-parser',
                'generator-novo'
               ]

tmp             = datetime.datetime.today()
todays_date_str = tmp.strftime("%Y-%m-%d")
todays_date     = tmp.strptime(todays_date_str, "%Y-%m-%d")

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
    """Wait for user input. Should only be run from CLI (not from Jenkins pipeline)"""
    global WAIT

    """Display 'msg' and wait for user to press enter"""
    print msg
    if WAIT:
        raw_input('Press Enter to continue')
    else:
        time.sleep(2)

def traverse(repo_name, data, catalog):
    """Traverse through folders looking for files. Recuersively calls itself until a folder has been processed"""

    if repo_name == 'dev':
        use_repo = DEV_PATH
    elif repo_name == 'rel':
        use_repo = REL_PATH
    else:
        if VERBOSE: print 'Invalid repo name (%s)' % repo_name
        return catalog

    for c in data['children']:  # Follow the children folders

        # Here we skip over entries in the SKIP_LIST. Since that only applies to 'dev' (we want a full record of 'rel'
        # we only do this when processing 'dev')
        if repo_name == 'dev':
            if len(SKIP_LIST) > 0:      # Make sure there is something to skip
                if re.findall(r"(?=("+'|'.join(SKIP_LIST)+r"))", data['path']): # See folder to skip is this folder, continue
                    if VERBOSE: print '=> skipping: %s' % data['uri']
                    user_input('Skip child')
                    return              # There was a match so return w/o further processing

        # If we are here, we have a folder to process.
        # Create the full path (new_path) with child name in case this is a folder we need to go deepeer into
        new_path = use_repo + data['path'] + c['uri']
        new_data = collect_data(new_path)           # Run the curl on the new path
        if c['folder']:                             # If the child is a folder, traverse it
            traverse(repo_name, new_data, catalog)  # Call myself with new path
        else:                                       # If not a folder, new_data contains the date info we need
            print 'saving file: %s' % c['uri']
            file = data['path'] + c['uri']          # File (after DEV_PATH start point)
            catalog[file] = new_data['created']     # Save created date in dict with <file> as key

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
    """Show the catalog's dictionary"""

    print '\nDisplay catalog, %d entries..' % len(cat)
    user_input('Press Enter when ready to view')
    for k in sorted(cat):
        print '%s :created on: %s' % (k, cat[k])

def save_catalog(dct, file):
    """Save the catalog dictionary (dct) to file"""

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
    """Delete the files from the delete list.
        See: https://en.wikipedia.org/wiki/List_of_HTTP_status_codes   for return codes
    """

    global VERBOSE

    for f in lst:
        file = DEV_PATH + f

        if DO_DELETE:
            if VERBOSE: print 'deleting "%s"' % file
            resp = requests.delete(file, auth=(u, p))
        else:
            if VERBOSE: print 'Issuing "get" for "%s"' % file
            resp = requests.get(file, auth=(u, p))

        if not resp.status_code <= 200 <= 299:  # Non-success values (success >= 200 < 300)
            print 'WARNING: ', resp
            user_input('A non-success value was returned!')
        user_input('GO')

def main():
    """Main loop of script"""

    global MAX_DAYS, VERBOSE, TEST_MODE, GEN_SAVED_DATA, SKIP_LIST, WAIT, DO_DELETE

    rel_catalog = dict()
    dev_catalog = dict()
    keep   = list()
    delete = list()

    parser = argparse.ArgumentParser(description='NPM artifact cleaner')
    parser.add_argument('-d', '--days', help='Remove files older than this value', type=int)
    parser.add_argument('-q', '--quick', help='Quick test (Done create dlete_list file', action='store_true')
    parser.add_argument('-v', '--verbose', help='Be verbose in processing', action='store_true')
    parser.add_argument('-g', '--generate', help='Dont generate saved data files', action='store_true')
    parser.add_argument('-t', '--test_mode', help='Use saved data and dont execute delete', action='store_true')
    parser.add_argument('-w', '--wait', help='Wait for user (should only be used on CLI)', action='store_true')
    parser.add_argument('-D', '--delete', help='Set this flag to actually delete the files', action='store_true')
    parser.add_argument('-S', '--skip', help='Comma seperated list of folders to add to internal SKIP_LIST', type=str)
    parser.add_argument('-u', '--user', help='username', required=True, type=str)
    parser.add_argument('-p', '--password', help='passwd', required=True, type=str)

    args = parser.parse_args()

    if args.days:
        MAX_DAYS = args.days
        if TEST_MODE:
            print 'TEST_MODE is set and you issued the "-d DAYS" option!'
            print 'TEST_MODE reads saved data calculated at 30 days, so this might not give the results you expect!'

    if args.test_mode: TEST_MODE = True
    if args.delete: DO_DELETE = True
    if args.verbose: VERBOSE = True
    if args.wait: WAIT = True
    if args.generate: GEN_SAVED_DATA = False    # Rely only on saved file data
    if args.skip:   # Add these items to our skip list
        new_list = args.skip.split(',')
        SKIP_LIST = SKIP_LIST + new_list
        print 'New list: ', SKIP_LIST
        if not GEN_SAVED_DATA:
            print 'Warning: "-S" should not be used with "-g" (since we will be generating new data)'


    user = args.user
    passwd = args.password

#    if DO_DELETE:
#        print '**WARNING you are actually going to delete data**'
#        user_input('WARN')

    if GEN_SAVED_DATA:  # Scan the folders and save the data (saved data used for debugging)
        print '\nGenerating npm-dev catalog'
        if len(SKIP_LIST):
            print '  and will skip: ', SKIP_LIST

        dev_base = collect_data(DEV_PATH)
        traverse('dev', dev_base, dev_catalog)
        save_catalog(dev_catalog, DEV_CATALOG)

        print '\nGenerating npm-release catalog'
        rel_base = collect_data(REL_PATH)
        traverse('rel', rel_base, rel_catalog)
        save_catalog(rel_catalog, REL_CATALOG)
    else:               # Son't scan repo, use data from previous run
        if VERBOSE: print 'Using saved data'
        dev_catalog = read_data(DEV_CATALOG)
        if VERBOSE: print '%d files read from %s' % (len(dev_catalog), DEV_CATALOG)
        rel_catalog = read_data(REL_CATALOG)
        if VERBOSE: print '%d files read from %s' % (len(rel_catalog), REL_CATALOG)
        user_input('Data is loaded in catalogs.')


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
