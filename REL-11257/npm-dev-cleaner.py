# This script MUST be called with python 2.x
#!/usr/bin/env python
#
# Version 1.0 (04/23/2020)

# Called by Jenkins pipeline
# http://hydrogen.bh-bos2.bullhorn.com/Release_Engineering/Miscellaneous-Tools/cboland-sandbox/Working_Pipelines/Artifactory-npm-dev-Cleaner/

import json
import re
import os
import datetime
import argparse
import requests
import sys
import time

# When set, we collect all the data via curl and save that data for future test runs. If the flag is False, we dont
# run the "curl" and rely on the data saved (initially, these curls are taking 10's of minutes).
GEN_SAVED_DATA = True   # By default we poll and save the data (False when debugging and we catalog files saved)

# User options (some are only available via CLI)
CLEAN     = True    # Clean any files I create (except the log) : CLI and Jenkins
DELETE_ONE = False  # Delete on file and exit : CLI and Jenkins (for now)
INTERACTIVE = False # Have user confirm deletion for each file (for debugging) : CLI
VERBOSE   = False   # Show what's being done : CLI and Jenkins
WAIT      = False   # Wait for user if true. : CLI
DO_DELETE = False   # Saftey measure. You MUST call script with "-D" to actually delete : CLI and Jenkins
MAX_DAYS  = 60      # Delete files older than this value : CLI and Jenkins

# This is what we process
BASE_PATH = 'http://artifactory.bullhorn.com:8081/artifactory/api/storage'
DEV_PATH  = BASE_PATH + '/npm-dev'
REL_PATH  = BASE_PATH + '/npm-release'

# Misc files generated
LOG_FILE     = 'log'                # timestamp and ".txt" are appended to this
DEV_CATALOG  = "dev_catalog.txt"    # Where I store npm-dev results
REL_CATALOG  = "release_catalog.txt"# Where I store npm-releas results
KEEP_FILES   = 'keepers.txt'        # Where I store files to keep
DELETE_FILES = 'deleters.txt'       # Where I store files to delete
SKIPPED_FILES = 'skipped.txt'       # Where I store files/folders to skip

# Skip the following folders in the npm-dev repo
# This list can be appended to via the "-S <list,of,folders>" option (comma seperated)
SKIP_LIST    = ['.npm/@bullhorn-internal',
                '.npm/@bullhorn',
                '.npm/bh-elements',
                '.npm/symphony-staffing',
                '.npm/chomsky',
                '.npm/galaxy-parser',
                '.npm-generator-novo'
                '@bullhorn-internal',
                '@bullhorn',
                'bh-elements',
                'symphony-staffing',
                'chomsky',
                'galaxy-parser',
                'generator-novo'
               ]

# Skip files that have this in the name. For now I skip variants of "DO_NOT_DELETE" in the file name
# This list can only be modified here (for now).
DO_NOT_DEL_LIST = ['DONOTDELETE',
                   'DO_NOT_DELETE',
                   'DONTDELETE',
                   'DONT_DELETE'
                  ]

tmp             = datetime.datetime.today()
todays_date_str = tmp.strftime("%Y-%m-%d")
timestamp       = tmp.strftime("%Y%m%d%H%M")
todays_date     = tmp.strptime(todays_date_str, "%Y-%m-%d")

skipped = list()

def collect_data(uri):
    """ Collect URI data via curl and return output in a dict.  """

    tmp_file = './.tempfile.txt'

    data = list()

    # Some file names have spaces and/or parenthesis and don't seem to play well with curl. I have tried escaping the
    # string with no luck. For now, a simpler solution is to wrap the uri in quotes. So I do that here
    curl_str = 'curl "' + uri + '" -o ' + tmp_file + " > /dev/null 2>&1"
    lprint ('Processing: %s' % uri, False)

    stat = os.system(curl_str)
    if stat != 0:
        msg = '* Warning: curl ' + uri + ' returned status: ' + stat
        lprint (msg, True)
        return data

    # This is what I get with those files with spaces, etc
    if os.stat(tmp_file).st_size == 0:
        lprint ('* Warning: curl returned no output for %s' % uri, True)
        return data

    # Curl appears to have run so read the output and store in a dict
    try:
        with open(tmp_file) as file_in:
            data = json.load(file_in)
    except IOError:
        lprint ('* Warning: could not read "%s"' % tmp_file, True)
        sys.exit(1)

    return data

def traverse(repo_name, data, catalog):
    """ Recursively traverse through folders looking for files. """

    global skipped

    # Determine which base path to use
    if repo_name == 'dev':
        use_repo = DEV_PATH
    elif repo_name == 'rel':
        use_repo = REL_PATH
    else:
        lprint ('* Warning: invalid repo name (%s)' % repo_name, True)
        return catalog

    # If the data has a 'children' key then I need to process it further
    for c in data['children']:

        # If processing the 'DEV' repo, check to see if this is a folder in the SKIP_LIST
        if repo_name == 'dev':
            if len(SKIP_LIST) > 0:      # Make sure there is something to skip

                # See if this folder (data['path']) is in our list of folders to skip (SKIP_LIST)
                # If so, skip it by returning
                if re.findall(r"(?=("+'|'.join(SKIP_LIST)+r"))", data['path']):
                    lprint ('! skipping: %s' % data['uri'], False)
                    skipped.append('Skip Folder: ' + data['uri'])
                    return(catalog)     # There was a match so return w/o further processing

        # If here, this is a valid file/folder to process
        # Create the full path (new_path) with child name. If it has a 'folder' key we traverse deeper. If not, this
        # must be a file and I obtain the creation date from the <new_path>
        new_path = use_repo + data['path'] + c['uri']
#        new_path = data['uri'] + c['uri']
        new_data = collect_data(new_path)               # Get data on new path

        # If nothing is returned that's an issue. Add it to the 'skip' list and log it as needing "Attention"
        if new_data == None:
            lprint ('! skip NULL dict: %s' % c['uri'], False)
            skipped.append('Attention: ' + data['uri'] + c['uri'])
        else:
            if c['folder']:                             # If the child is a folder
                traverse(repo_name, new_data, catalog)  # traverse to the next level
            else:                                       # If not a folder, new_data contains the date info we need

                # If a file name contans "DO_NOT_DELETE" (or some variant thereof) skip it
                if re.findall(r"(?=("+'|'.join(DO_NOT_DEL_LIST)+r"))", c['uri']):
                    lprint ('! skipping: %s' % c['uri'], True)              # Show file (only)
                    skipped.append('Skip File: ' + data['uri'] + c['uri'])  # Save full path
                    return(catalog)

                file = data['path'] + c['uri']          # File, relative to BASE

                # In some cases curl is returning no date info and I haven't been able to figure out why. It seems
                # to occur on files with spaces and/or parenthesis in the file name. In any case, until I figure it
                # out, I will mark the file as 'skipped'.
                if len(new_data) == 0:
                    msg = '! ignore: no date info found for: ' + data['uri'] + c['uri']
                    lprint (msg, False)
                    skipped.append('Null date: ' + data['uri'] + c['uri'])
                else:
                    catalog[file] = new_data['created']     # Save created date in dict with <file> as key

    return(catalog)

def read_data(file):
    """ Read the outut of a real run. Each line is a K|V pair to repopulate the dicts """
    data = list()
    dct  = dict()

    lprint ('Reading "%s"' % file, False)
    if not os.path.exists(file):
        lprint ('"%s" not found! (try running w/o "-g" option) .. exiting' % file, False)
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
            lprint ('  "%s" does not have two fields (%d) .. skipping' % (x, len(x.split('|'))), False)
            continue

        (k, v) = x.split("|")
        dct[k] = v

    return(dct) # Return the new dictionary

# For debugging, so I don't log this
def show_catalog(cat):
    """Show the catalog's dictionary"""

    print '\nDisplay catalog, %d entries..' % len(cat)
    raw_input('Press Enter when ready to view')
    for k in sorted(cat):
        print '%s :created on: %s' % (k, cat[k])

def save_catalog(dct, file):
    """Save the catalog dictionary (dct) to file"""

    lprint ('Saving catalog "%s"' % file, False)
    with open(file, 'w') as fo:
        for k in sorted(dct):
            fo.write('%s|%s\n' % (k, dct[k]))

def write_list(file, lst):
    """Write a list to file"""

    lprint ('Writing list to %s' % file, False)
    with open(file, 'w') as file_ptr:
        for k in sorted(lst):
            file_ptr.write('%s\n' % k)

def delete_files(lst, u, p):
    """Delete the files from the delete list.
        See: https://en.wikipedia.org/wiki/List_of_HTTP_status_codes   for return codes
    """
    user_skip = False

    lprint('Deleting files..', False)
    for f in lst:
        file = DEV_PATH + f

        if DO_DELETE:
#            lprint('  formatting path for delete ..', False)

            # To actually delete the file we must reformat the path we aquired and remove the string '/api/storage'
            # from the path. If we do not do this calls to delete will return "400" (bad request).
            file = file.replace('/api/storage', '')
            lprint('  "%s"' % f, False) # Show the file to be deleted

            if INTERACTIVE:     # VERBOSE is set to True when this is selected
                user_skip = False
                lprint ('%s will be deleted next' % file, False)
                ans = raw_input('Ok to delete [y/n/q]: ')
                if 'y' in ans:
                    lprint ('deleteing "%s"' % file, False)
#                    resp = requests.get(file, auth=(u, p))
                    resp = requests.delete(file, auth=(u, p))
                elif 'q' in ans:
                    return
                else:
                    lprint ('skipping "%s"' % file, False)
                    user_skip = True
            elif DELETE_ONE:
#                resp = requests.get(file, auth=(u, p))
                resp = requests.delete(file, auth=(u, p))
                if not 200 <= resp.status_code <= 299:  # Success values (200-299)
                    lprint ('* Warning: %s' % resp, False)
                    lprint ('  a non-success value was returned!', True)
                else:
                    lprint ('request status returned: %d' % resp.status_code, True)

                return
            else:
                lprint ('deleteing "%s"' % file, False)
#                resp = requests.get(file, auth=(u, p))
                resp = requests.delete(file, auth=(u, p))
        else:
            lprint ('"get" "%s"' % file, False)
            resp = requests.get(file, auth=(u, p))

        if user_skip == False:
            if not 200 <= resp.status_code <= 299:  # Success values (200-299)
                lprint ('* Warning: %s' % resp, False)
                lprint ('  a non-success value was returned!', True)
            else:
                lprint ('request status returned: %d' % resp.status_code, True)

def parse_options():
    """Parse options that are set in the environment (from Jenkins)"""

    global VERBOSE, SKIP_LIST, MAX_DAYS, CLEAN, DO_DELETE, DELETE_ONE

    tmp = os.getenv("VERBOSE")
    if tmp and tmp.lower() in ['true', '1']:
        VERBOSE = True

    tmp = os.getenv("DELETE_ONE")               # Used as a test. Run scans but exit after deleting one file
    if tmp and tmp.lower() in ['true', '1']:
        DELETE_ONE = True

    tmp = os.getenv("DO_DELETE")               # Used as a test. Run scans but exit after deleting one file
    if tmp and tmp.lower() in ['true', '1']:
        DO_DELETE = True

    tmp = os.getenv("KEEP_FILES")
    if tmp and tmp.lower() in ['true', '1']:
        CLEAN = False

    tmp = os.getenv("SKIP_LIST")
    if tmp:
        SKIP_LIST = tmp.split(',')

    tmp = os.getenv("MAX_DAYS")
    if tmp:
        MAX_DAYS = int(tmp)

def cleanup_temp_files():
    """Clean up temp files"""

    files = ['./.tempfile.txt', KEEP_FILES, DELETE_FILES, SKIPPED_FILES, DEV_CATALOG, REL_CATALOG]

    for f in files:
        if os.path.exists(f):
            lprint ('rm ' + f, False)
            try:
                os.system('rm ' + f)
            except:
                lprint ('  could not remove %s' % f, False)

def lprint(msg, wait):
    """Log and print a message"""
    global timestamp

    log_file =  LOG_FILE + '-' + timestamp + '.txt'

    with open(log_file, 'a') as lf:
        lf.write(msg + '\n')

    if VERBOSE:                     # Verbose is set
        if wait:                    # And we requested user input
            if WAIT:                # And the WAIT option was issued
                raw_input(msg)      # So, wait for user
            else:                   # WAIT not issued
                print msg           # Show message
                sys.stdout.flush()  # Make sure we flush the msg before sleeping
                time.sleep(5)       # And delay (instead of wait)
        else:
            print msg               # Else, just print message
    else:
        if re.match(r'\* Warning', msg, re.IGNORECASE):
            print msg
    sys.stdout.flush()              # One final flush for the rest


def main():
    """Main loop of script"""

    global MAX_DAYS, VERBOSE, INTERACTIVE, GEN_SAVED_DATA, SKIP_LIST, WAIT, DO_DELETE, CLEAN
    global skipped

    rel_catalog = dict()
    dev_catalog = dict()
    keep   = list()
    delete = list()
    skipped = []

    # These are only used for running on CLI. Jenkins passes its params (except creds) via env-vars in OS
    parser = argparse.ArgumentParser(description='NPM artifact cleaner')
    parser.add_argument('-d', '--days', help='Remove files older than this value', type=int)
    parser.add_argument('-c', '--clean', help='Dont cleanup temp files', action='store_true')
    parser.add_argument('-o', '--delete_one', help='Delete one file and exit', action='store_true')
    parser.add_argument('-i', '--interactive', help='User confrims each delete', action='store_true')
    parser.add_argument('-v', '--verbose', help='Be verbose in processing', action='store_true')
    parser.add_argument('-g', '--generate', help='Dont generate saved data files', action='store_true')
    parser.add_argument('-w', '--wait', help='Wait for user (should only be used on CLI)', action='store_true')
    parser.add_argument('-D', '--delete', help='Set this flag to actually delete the files', action='store_true')
    parser.add_argument('-S', '--skip', help='Comma seperated list of folders to add to internal SKIP_LIST', type=str)
    parser.add_argument('-u', '--user', help='username', required=True, type=str)
    parser.add_argument('-p', '--password', help='passwd', required=True, type=str)

    args = parser.parse_args()
    user = args.user
    passwd = args.password

    if args.days:
        MAX_DAYS = args.days

    # If we have CLI options we must set an env-var to be consistent.
    # options not set as env-var are options Jenkins doesn't have
    if args.interactive:
        INTERACTIVE = True  # Confirm each delete
        VERBOSE = True      # We have to see what we're doing
        WAIT = True         # Wait for user input
        CLEAN = False       # Dont delete files
    if args.delete_one:
        os.environ["DELETE_ONE"] = "1"
    if args.delete:
        os.environ["DO_DELETE"] = "1"
    if args.verbose:
        os.environ["VERBOSE"] = "1"
    if args.clean:
        os.environ["KEEP_FILES"] = "1"
    if args.wait:
        WAIT = True
    if args.generate:
        GEN_SAVED_DATA = False  # Rely only on saved file data (for debugging)
    if args.skip:               # Add these items to our skip list
        new_list = args.skip.split(',')
        SKIP_LIST = SKIP_LIST + new_list
        os.environ["SKIP_LIST"] = ','.join(SKIP_LIST)
        if not GEN_SAVED_DATA:
            print 'Warning: "-S" should not be used with "-g" (since we will be generating new data)'

    parse_options()     # Parse any env-var options Jenkins sent

    if DO_DELETE:
        lprint ('** Delete option is set **', True)
        if DELETE_ONE:
            lprint ('** Delete one file and exit is also set **', False)

    # Log, and maybe show, which options were called
    lprint ('\nScript called with these options..', False)
    lprint ('    VERBOSE: %s' % VERBOSE, False)
    lprint ('  DO_DELETE: %s' % DO_DELETE, False)
    lprint (' DELETE_ONE: %s' % DELETE_ONE, False)
    lprint ('INTERACTIVE: %s' % INTERACTIVE, False)
    lprint ('   MAX_DAYS: %d' % MAX_DAYS, False)
    lprint ('CLEAN UP FILES: %s' % CLEAN, False)
    lprint (' SKIP_LIST: %s' % SKIP_LIST, False)
    lprint ('', False)

    # I could process the data w/o saving it but the data is useful for debugging and running multiple time
    # without having to constantly send requests to artifactory
    if GEN_SAVED_DATA:  # Scan the artifactory folders and save the data
        lprint ('\nGenerating npm-dev catalog\n==========================', False)
        if len(SKIP_LIST):
            msg = ', '.join(SKIP_LIST)
            lprint ('Folders to skip: %s' % msg, False)
            lprint ('----------------------------------------------------------------------------------------------', False)

        dev_base = collect_data(DEV_PATH)
        traverse('dev', dev_base, dev_catalog)
        save_catalog(dev_catalog, DEV_CATALOG)

        lprint ('\nGenerating npm-release catalog\n==============================', False)
        rel_base = collect_data(REL_PATH)
        traverse('rel', rel_base, rel_catalog)
        save_catalog(rel_catalog, REL_CATALOG)
    else:               # Don't scan artifactory, use data from previous run
        lprint ('Using saved data', False)
        dev_catalog = read_data(DEV_CATALOG)
        lprint ('%d files read from %s' % (len(dev_catalog), DEV_CATALOG), False)
        rel_catalog = read_data(REL_CATALOG)
        lprint ('%d files read from %s' % (len(rel_catalog), REL_CATALOG), True)


    # Now that I have all the development and release files, with their creation dates, it's time to process them
    # Files > MAX_DAYS and are NOT in the release catalog can be deleted
    for dev_file in sorted(dev_catalog):    # Loop through the development files
        lprint ('Processing: %s' % dev_file, False)
        if not dev_file in rel_catalog:     # If dev file is NOT in the release catalog, check date, etc
            tmp           = re.search(r'(.*)(-\d{2,}:\d{2,})', dev_catalog[dev_file])    # Strip off timezone
            tmp_time      = tmp.groups()[0]                                              # Save string w/o TZ
            file_dt       = datetime.datetime.strptime(tmp_time, '%Y-%m-%dT%H:%M:%S.%f') # Convert dev_file to datetime obj
            file_date_str = datetime.datetime.strftime(file_dt, '%Y-%m-%d')              # Create a 'date' (only) string
            file_date     = datetime.datetime.strptime(file_date_str, '%Y-%m-%d')        # Create a 'date' (only) object
            delta         = todays_date - file_date

            if delta.days > MAX_DAYS:
                lprint ('  -> file is not in releases, is %d days old (%d is cutoff) .. marked for removal' % (delta.days, MAX_DAYS), False)
                delete.append(dev_file)         # Put this file in the delete list
            else:
                lprint ('  -> file is not in releases, but only %d days old (%d is cutoff) .. file kept' % (delta.days, MAX_DAYS), False)
                keep.append(dev_file)           # Put this file in the keep list
        else:
            lprint ('    -> file is listed in release catalog and will be kept', False)
            keep.append(dev_file)           # Put this file in the keep list

    lprint ('', False)
    write_list(KEEP_FILES, keep)
    write_list(DELETE_FILES, delete)
    write_list(SKIPPED_FILES, skipped)
    lprint ('', False)
    if not DO_DELETE:
        lprint ('DO_DELETE was NOT issued!  I will perform a "get" operation to test functionality.', False)
    lprint ('', False)

    delete_files(delete, user, passwd)

    if CLEAN:      # Clean temp files unless user said 'no'
        cleanup_temp_files()
    else:
        lprint ('\nKeeping temporary files', False)

    lprint ('\nJob complete\n', False)

if __name__ == '__main__':
    main()
