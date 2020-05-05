# This script MUST be called with python 2.x
#!/usr/bin/env python
#
# Version 1.1.8 (05/05/2020)

# Called by Jenkins pipeline
# http://hydrogen.bh-bos2.bullhorn.com/Release_Engineering/Miscellaneous-Tools/cboland-sandbox/Working_Pipelines/Artifactory-npm-dev-Cleaner/

# Local storage: devartifactory1 : /art-backups/current/repositories/npm-dev/

import json
import re
import os
import datetime
import argparse
import requests
import sys
import time
import subprocess
import shlex

# When set, we collect all the data via curl and save that data for future test runs. If the flag is False, we dont
# run the "curl" and rely on the data saved (initially, these curls are taking 10's of minutes).
GEN_SAVED_DATA = True   # By default we poll and save the data (False when debugging and we use saved catalog files)

# User options (some are only available via CLI)
DELETE_ONE = False  # Delete on file and exit : CLI and Jenkins (for now)
INTERACTIVE = False # Have user confirm deletion for each file (for debugging) : CLI
CLEAN     = True    # Clean any files I create (except the log) : CLI and Jenkins
LOG_DATA  = True
VERBOSE   = False   # Show what's being done : CLI and Jenkins
WAIT      = False   # Wait for user if true. : CLI
DO_DELETE = False   # Saftey measure. You MUST call script with "-D" to actually delete : CLI and Jenkins
MAX_DAYS  = 60      # Delete files older than this many days : CLI and Jenkins
USE_MODIFIED_TIME = True
USE_CREATED_TIME = False

# This is what we process
BASE_PATH = 'http://artifactory.bullhorn.com:8081/artifactory/api/storage'
DEV_PATH  = BASE_PATH + '/npm-dev'      # Maybe future versions will have these be a param from Jenkins
REL_PATH  = BASE_PATH + '/npm-release'

# Misc files generated
LOG_FILE     = 'log'                # timestamp and ".txt" are appended to this
DEV_CATALOG  = 'dev_catalog.txt'    # Where I store npm-dev results
REL_CATALOG  = 'release_catalog.txt'# Where I store npm-release results
KEEP_FILES   = 'keepers.txt'        # File NOT found in release repo but to young to delete.
IN_REL_FILES = 'in_release_repo.txt'# Files found in release repo (to keep)
DELETE_FILES = 'deleters.txt'       # Where I store files to delete
SKIPPED_FILES = 'skipped.txt'       # Where I store files/folders to skip (matched SKIP_LIST)

# Skip the following FOLDERS in the npm-dev repo
# This list can be appended to via the "-S <list,of,folders>" option (comma seperated)
SKIP_LIST    = ['.npm/@bullhorn-internal', '.npm/@bullhorn',     '.npm/bh-elements',
                '.npm/symphony-staffing',  '.npm/chomsky',       '.npm/galaxy-parser',
                '.npm-generator-novo',     '@bullhorn-internal', '@bullhorn',
                'bh-elements',             'symphony-staffing',  'chomsky',
                'galaxy-parser',           'generator-novo'
               ]

# Skip FILES in this list. For now I skip variants of "DO_NOT_DELETE" in the file name, and package.json
# This list can only be modified here (for now).
DO_NOT_DEL_LIST = ['DONOTDELETE',   'DO_NOT_DELETE',   'DONTDELETE',
                   'DONT_DELETE',   'package.json'
                  ]

tmp             = datetime.datetime.today()
todays_date_str = tmp.strftime("%Y-%m-%d")
timestamp       = tmp.strftime("%Y%m%d%H%M")
todays_date     = tmp.strptime(todays_date_str, "%Y-%m-%d")

skipped = list()

def collect_data(uri):
    """ Collect URI data via curl and return output in a dict.  """

    data = list()

    # Some file names have spaces and/or parenthesis and don't seem to play well with curl. I have tried escaping the
    # string with no luck. For now, a simpler solution is to wrap the uri in quotes. So I do that here
    curl_str = 'curl "' + uri + '"'
    lprint ('Processing: %s' % uri, False)

    args = shlex.split(curl_str)                                    # Convert cmd to shell-like syntax
    with open(os.devnull, 'w') as DEV_NULL:                         # Open file descriptor to /dev/null
        try:                                                        # Try and run the curl command
            out = subprocess.check_output(args, stderr=DEV_NULL)    # If success, "out" has our data
        except subprocess.CalledProcessError as e:                  # Report issues the process had
            lprint('subprocess ERROR %s' % e.output, False)         # Print that exception here
        except:                                                     # Grab all other exceptions here
            lprint('Unknown ERROR: Sys: %s' % sys.exc_info()[0], False) # And try to show what caused the issue
        else:                                                           # No exception, so continue processing..
            try:                                                        # Try and convert "out" data to JSON
                data = json.loads(out)                                  # Ok, we converted to JSON
                if 'errors' in data:                                    # Sometimes the curl worked but we get bad data
                    lprint('Curl request returned an error: %s' % data, False)  # Show the error returned
                    data = list()                                       # Return empty dict
            except ValueError as e:                                     # Those pesky files don't product any output :(
                lprint('ERROR: ValuerError. Could not convert data to JSON', False) # So log it and move on
            except:                                                     # Grab all other exceptions here
                lprint('Unknown ERROR: Sys: %s' % sys.exc_info()[0], False)  # Get error from system

    return data         # Return the data dict (whether it has data or is None)

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
                    lprint ('! skipping folder: %s' % data['uri'], False)
                    skipped.append('Skip Folder: ' + data['uri'])
                    return(catalog)     # There was a match so return w/o further processing

        # If here, this is a valid file/folder to process
        # Create the full path (new_path) with child name. If it has a 'folder' key we traverse deeper. If not, this
        # must be a file and I obtain the creation date from the <new_path>
        new_path = use_repo + data['path'] + c['uri']
        new_data = collect_data(new_path)               # Get data on new path

        # If nothing is returned that's an issue. Add it to the 'skip' list and log it as needing "Attention"
        if new_data == None:
            lprint ('! skipping null dict: %s' % c['uri'], False)
            skipped.append('Attention: ' + data['uri'] + c['uri'])
        else:
            if c['folder']:                             # If the child is a folder
                traverse(repo_name, new_data, catalog)  # traverse to the next level
            else:                                       # If not a folder, new_data contains the date info we need

                if repo_name == 'dev':              # Only process items in dev catalog
                    if len(DO_NOT_DEL_LIST) > 0:    # Make sure there is something to skip
                # If a file name contans "DO_NOT_DELETE" (or some variant thereof), or 'package.json, skip it
                        if re.findall(r"(?=("+'|'.join(DO_NOT_DEL_LIST)+r"))", c['uri']):
                            lprint ('! skipping file: %s' % c['uri'], False)        # Show file (only) for readability
                            skipped.append('Skip File: ' + data['uri'] + c['uri'])  # Save full path
                            return(catalog)

                file = data['uri'] + c['uri']          # File, full path

                # In some cases curl is returning no date info and I haven't been able to figure out why. It seems
                # to occur on files with spaces and/or parenthesis in the file name. In any case, until I figure it
                # out, I will mark the file as 'skipped'.
                if len(new_data) == 0:
                    lprint ('! ignore: no date found (%s)' % file, False)
                    if repo_name == 'dev':              # Only process items in dev catalog
                        lprint ('! skipping null date: %s' % c['uri'], False)   # Show file (only) for readability
                        skipped.append('Null date: ' + data['uri'] + c['uri'])
                else:
            # I think we should be uising the lastModified date instead of created date. Some files have a
            # created date of years ago (package.json) while its lastModified date is within a day of current date
                    if USE_MODIFIED_TIME:
                        catalog[file] = new_data['lastModified'] # Save modified date in dict with <file> as key
                    else:
                        catalog[file] = new_data['created']      # Save created date in dict with <file> as key

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

# For debugging, so I don't log this output
def show_catalog(cat):
    """ Show the catalog's dictionary """

    print '\nDisplay catalog, %d entries..' % len(cat)
    raw_input('Press Enter when ready to view')
    for k in sorted(cat):
        print '%9s :: %s' % (cat[k], k)
#        print '%s :lastModifed on: %s' % (k, cat[k])
#        print '%s :created on: %s' % (k, cat[k])

def save_catalog(dct, file):
    """ Save the catalog dictionary (dct) to file """

    lprint ('Saving catalog "%s"' % file, False)
    with open(file, 'w') as fo:
        for k in sorted(dct):
            fo.write('%s|%s\n' % (k, dct[k]))

def write_list(file, lst):
    """ Write a list to file """

    lprint ('Writing list to %s' % file, False)
    with open(file, 'w') as file_ptr:
        # Since we sort the list when we write I can't merely insert these comments into the list. I must write them to
        # the file then write the sorted data behind it.
        if file == SKIPPED_FILES:
            file_ptr.write("# These files were skipped because..\n")
            file_ptr.write("# 1) There was an issue processing the file date\n")
            file_ptr.write("# 2) The folder/file matches one of these names..\n")
            tmp = '# {}'.format(', '.join(DO_NOT_DEL_LIST))
            file_ptr.write('%s\n' % tmp)
            file_ptr.write("#\n# 3) The folder/file matches one of these names..\n")
            tmp = '# {}'.format(', '.join(SKIP_LIST))
            file_ptr.write('%s\n' % tmp)
            file_ptr.write("#\n################################################\n")
        elif file == KEEP_FILES:
            file_ptr.write("# These files were NOT found in the release repo but are < %d days old (will be kept)\n" % MAX_DAYS)
            file_ptr.write("##########################################################################################\n")
        elif file == IN_REL_FILES:
            file_ptr.write("# These files are found in both the dev repo AND release repo and will NOT be removed\n")
            file_ptr.write("#####################################################################################\n")
        elif file == DELETE_FILES:
            file_ptr.write("# These files are marked for deletion because they are NOT in the release repo,\n")
            file_ptr.write("# are NOT in one of the skip lists and their lastModified date is > %d days\n" % MAX_DAYS)
            file_ptr.write("################################################################################\n")

        for k in sorted(lst):
            file_ptr.write('%s\n' % k)

def delete_files(lst, u, p):
    """ Delete the files from the delete list.
        See: https://en.wikipedia.org/wiki/List_of_HTTP_status_codes   for return codes
    """
    user_skip = False

    lprint('%d files to delete ..' % len(lst), False)
    for file in lst:
        if file.startsWith('#'):    # Skip any comment in the file
            continue

        if DO_DELETE:

            # To delete the file we must reformat the path aquired and remove the string '/api/storage'
            # from the path. If we do not do this calls to delete will return "400" (bad request).
            file = file.replace('/api/storage', '')
#            lprint('  "%s"' % file, False) # Show the file to be deleted

            if INTERACTIVE:     # VERBOSE is set to True when this is selected
                user_skip = False
                lprint ('%s' % file, False)
                ans = raw_input('Ok to delete [y/n/q]: ')
                if 'y' in ans:
                    lprint ('deleteing "%s"' % file, False)
                    resp = requests.delete(file, auth=(u, p))
                elif 'q' in ans:
                    return
                else:
                    lprint ('skipping "%s"' % file, False)
                    user_skip = True
            elif DELETE_ONE:
                resp = requests.delete(file, auth=(u, p))
                if not 200 <= resp.status_code <= 299:  # Success values (200-299)
                    lprint ('* Warning: %s' % resp, False)
                    lprint ('  a non-success value was returned!', True)
                else:
                    lprint ('request status returned: %d' % resp.status_code, True)

                return
            else:
                lprint ('deleteing "%s"' % file, False)
                resp = requests.delete(file, auth=(u, p))
        else:
            lprint ('"get" "%s"' % file, False)
            resp = requests.get(file, auth=(u, p))

        if user_skip == False:
            if not 200 <= resp.status_code <= 299:  # Success values (200-299)
                lprint ('* Warning: %s' % resp, False)
                lprint ('  a non-success value was returned!', True)
            else:
                lprint ('request status returned: %d' % resp.status_code, False)

def parse_options():
    """ Parse options that are set in the environment (from Jenkins) """

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

    tmp = os.getenv("USE_CREATED_TIME")
    if tmp and tmp.lower() in ['true', '1']:
        USE_CREATED_TIME = True
        USE_MODIFIED_DATE = False

    tmp = os.getenv("SKIP_LIST")
    if tmp:
        SKIP_LIST = tmp.split(',')

    tmp = os.getenv("MAX_DAYS")
    if tmp:
        MAX_DAYS = int(tmp)

def cleanup_temp_files():
    """ Clean up temp files """

    files = [KEEP_FILES, DELETE_FILES, SKIPPED_FILES, DEV_CATALOG, REL_CATALOG]

    for f in files:
        if os.path.exists(f):
            lprint ('rm ' + f, False)
            try:
                os.system('rm ' + f)
            except:
                lprint ('  could not remove %s' % f, False)

def lprint(msg, wait):
    """ Log and print a message """
    global timestamp

    log_file =  LOG_FILE + '-' + timestamp + '.txt'

    if LOG_DATA:
        with open(log_file, 'a') as lf:
            lf.write(msg + '\n')

    if VERBOSE:                     # Verbose is set
        if wait:                    # And we requested user input
            if WAIT:                # And the WAIT option was issued
                raw_input(msg)      # So, wait for user
            else:                   # WAIT not issued
                print msg           # Show message
                sys.stdout.flush()  # Make sure we flush the msg before sleeping
                time.sleep(2)       # And delay (instead of wait)
        else:
            print msg               # Else, just print message
    else:
        if re.match(r'\* Warning', msg, re.IGNORECASE):
            print msg
    sys.stdout.flush()              # One final flush for the rest


def main():
    """ Main loop of script """

    global MAX_DAYS, VERBOSE, INTERACTIVE, GEN_SAVED_DATA, SKIP_LIST, WAIT, DO_DELETE, CLEAN
    global skipped

    rel_catalog = dict()
    dev_catalog = dict()
    keep        = list()
    rel_list    = list()
    in_release  = list()
    delete      = list()
    skipped     = []

    # These are only used for running on CLI. Jenkins passes its params (except creds) via env-vars in OS
    parser = argparse.ArgumentParser(description='NPM artifact cleaner')
    parser.add_argument('-d', '--days', help='Remove files older than this value', type=int)
    parser.add_argument('-c', '--create_time', help='Use file created time (instead of lastModified)', action='store_true')
    parser.add_argument('-k', '--keep_file', help='Dont keep_file temp files', action='store_true')
    parser.add_argument('-o', '--delete_one', help='Delete one file and exit', action='store_true')
    parser.add_argument('-i', '--interactive', help='User confrims each delete', action='store_true')
    parser.add_argument('-v', '--verbose', help='Be verbose in processing', action='store_true')
    parser.add_argument('-g', '--generate', help='Dont generate saved data files', action='store_true')
    parser.add_argument('-w', '--wait', help='Wait for user (should only be used on CLI)', action='store_true')
    parser.add_argument('-D', '--delete', help='Set this flag to actually delete the files', action='store_true')
    parser.add_argument('-S', '--skip', help='Comma seperated list of folders to add to internal SKIP_LIST', type=str)
    parser.add_argument('-u', '--user', help='username', required=True, type=str)
    parser.add_argument('-p', '--password', help='passwd', required=True, type=str)

    args   = parser.parse_args()
    user   = args.user
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
    if args.keep_file:
        os.environ["KEEP_FILES"] = "1"
    if args.create_time:
        os.environ["USE_CREATED_TIME"] = "1"
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
    lprint ('       WAIT: %s' % WAIT, False)
    lprint ('    VERBOSE: %s' % VERBOSE, False)
    lprint ('  DO_DELETE: %s' % DO_DELETE, False)
    lprint (' DELETE_ONE: %s' % DELETE_ONE, False)
    lprint ('INTERACTIVE: %s' % INTERACTIVE, False)
    lprint ('   MAX_DAYS: %d' % MAX_DAYS, False)
    lprint ('   CLEAN UP FILES: %s' % CLEAN, False)
    lprint (' USE_CREATED_TIME: %s' % USE_CREATED_TIME, False)
    lprint ('USE_MODIFIED_TIME: %s' % USE_MODIFIED_TIME, False)
    lprint ('\n SKIP_LIST: %s' % SKIP_LIST, False)
    lprint ('\nSKIP_FILES: %s' % DO_NOT_DEL_LIST, False)
    lprint ('', True)

    # I could process the data w/o saving it but the data is useful for debugging and running multiple time
    # without having to constantly send requests to artifactory
    if GEN_SAVED_DATA:  # Scan the artifactory folders and save the data
        lprint ('\nGenerating npm-dev catalog\n==========================', False)
        if len(SKIP_LIST):
            msg = ', '.join(SKIP_LIST)
            lprint ('Folders to skip: %s' % msg, False)
            lprint ('----------------------------------------------------------------------------------------------', False)

        lprint ('Call collect_data(DEV_PATH)', False)
        dev_base = collect_data(DEV_PATH)
        lprint ('Call traverse("dev", dev_base, dev_catalog)', False)
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

    # Now that I have all the development and release files, with their creation dates, it's time to process them.
    # Files > MAX_DAYS and are NOT in the release catalog AND are NOT in the SKIP LISTSs can be deleted
    rel_list = rel_catalog.keys()   # Convert dict to list of keys
    for dev_file in sorted(dev_catalog):    # Loop through the development files
        lprint ('Processing: %s' % dev_file, False)
        file_name = dev_file.split('/')[-1] # Just the file with no path

        # When we search for the file_name in the release catalog we have to take into account that the file name
        # might be common in multiple paths, which we don't want. The easiest way
        # for that search is to merely change the key's path from "npm-dev" to "npm-release". If that key
        # exists in the release_catalog, we know not to remove it from the dev_catalog

        # Convert the dev key to a possible release key
        rel_to_chk = dev_file.replace('/npm-dev/', '/npm-release/')

        # See if our new key matches ANY entries in the list of release keys
        if any (rel_to_chk in rl for rl in rel_list):
            lprint ('    -> "%s" is listed in release catalog and will be kept' % file_name, False)

            # As a sanity check I verify the key we're using IS a key int he release catalog. It would be
            # very odd if it weren't, but better to be safe than sorry.
            if not rel_to_chk in rel_catalog.keys():
                lprint('ERROR: Could not verify key (%s)' % rel_to_chk, False)
                skipped.append(dev_file)    # Err on the side of caution and dont delete the file.
            else:
                in_release.append(dev_file) # Add to list of files ALSO found in the release repo
        else:
            tmp           = re.search(r'(.*)(-\d{2,}:\d{2,})', dev_catalog[dev_file])    # Strip off timezone
            tmp_time      = tmp.groups()[0]                                              # Save string w/o TZ
            file_dt       = datetime.datetime.strptime(tmp_time, '%Y-%m-%dT%H:%M:%S.%f') # Convert dev_file to datetime obj
            file_date_str = datetime.datetime.strftime(file_dt, '%Y-%m-%d')              # Create a 'date' (only) string
            file_date     = datetime.datetime.strptime(file_date_str, '%Y-%m-%d')        # Create a 'date' (only) object
            delta         = todays_date - file_date

            if delta.days > MAX_DAYS:
                lprint ('  -> file is NOT in releases, is %d days old (%d is cutoff) .. marked for removal' % (delta.days, MAX_DAYS), False)
                delete.append(dev_file)         # Put this file in the delete list
            else:
                lprint ('  -> file is not in releases, but only %d days old (%d is cutoff) .. file kept' % (delta.days, MAX_DAYS), False)
                keep.append(dev_file)           # Files NOT in release repo but too yuong

    lprint ('', False)
    lprint('%4d entries in npm-release repo' % len(rel_catalog), False)
    lprint('%4d entries in npm-dev repo' % len(dev_catalog), False)
    lprint('%4d entries skipped (from SKIP_LIST)' % len(skipped), False)
    lprint('%4d entries skipped (Found in release repo)' % len(in_release), False)
    lprint('%4d entries kept (Too young)' % len(keep), False)
    lprint('%4d entries to delete' % len(delete), False)
    lprint ('', False)
    write_list(KEEP_FILES, keep)
    write_list(DELETE_FILES, delete)
    write_list(IN_REL_FILES, in_release)
    write_list(SKIPPED_FILES, skipped)
    lprint ('', False)
    if not DO_DELETE:
        lprint ('File deletion skipped', False)
    else:
        delete_files(delete, user, passwd)

    lprint ('', False)

    if CLEAN:      # Clean temp files unless user said 'no'
        cleanup_temp_files()
    else:
        lprint ('\nKeeping temporary files', False)

    lprint ('\nJob complete\n', False)

if __name__ == '__main__':
    main()
