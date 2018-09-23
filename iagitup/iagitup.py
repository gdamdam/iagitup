#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import unicode_literals

__author__     = "Giovanni Damiola"
__copyright__  = "Copyright 2018, Giovanni Damiola"
__main_name__  = 'iagitup'
__license__    = 'GPLv3'
__version__    = "v1.6"

import os
import sys
import subprocess
import shutil
import json
import internetarchive
import internetarchive.cli
import git
import requests
from datetime import datetime
from markdown2 import markdown_path


def mkdirs(path):
	"""Make directory, if it doesn't exist."""
	if not os.path.exists(path):
		os.makedirs(path)

# download the github repo
def repo_download(github_repo_url):
    """Downloads a GitHub repo locally.
    
       arguments:
            github_repo_url -- the GitHub repo home url

       returns:
            gh_repo_data, repo_folder - the repo details and the local repo folder
    """
    download_dir = os.path.expanduser('~/.iagitup/downloads')
    mkdirs(os.path.expanduser('~/.iagitup'))
    mkdirs(download_dir)

    # parsing url to initialize the github api rul and get the repo_data
    gh_user, gh_repo = github_repo_url.split('/')[3:]
    gh_api_url = "https://api.github.com/repos/{}/{}".format(gh_user,gh_repo)

    # delete the temp directory if exists
    repo_folder = download_dir+'/'+gh_repo
    if os.path.exists(repo_folder):
        shutil.rmtree(repo_folder)

    # get the data from GitHub api
    req = requests.get(gh_api_url)
    if req.status_code == 200:
        gh_repo_data = json.loads(req.text)
        # download the repo from github
        repo_folder = '{}/{}'.format(download_dir,gh_repo)
        try:
            git.Git().clone(gh_repo_data['clone_url'],repo_folder)
        except Exception as e:
            print 'Error occurred while downloading: {}'.format(github_repo_url)
            print str(e)
            exit(1)
    else:
        raise ValueError('Error occurred while downloading: {}'.format(github_repo_url))

    return gh_repo_data, repo_folder


def get_description_from_readme(gh_repo_folder):
    """From the GitHub repo returns html description from the README.md or readme.txt

        arguments:  
                gh_repo_folder -- the repo local folder path 

        returns:
                description -- html description 
    """
    path = '{}/{}'.format(gh_repo_folder,'README.md')
    path2 = '{}/{}'.format(gh_repo_folder,'readme.txt')
    description = ''
    if os.path.exists(path):
        description = markdown_path(path)
        description = description.replace('\n','')
    elif os.path.exists(path2):
        with open(path2,'r') as f:
            description = f.readlines()
            description =' '.join(description)
    return description

def create_bundle(gh_repo_folder, repo_name):
    """creates the gir repository bundle to upload
        
        arguments:
            gh_repo_folder  --  the repo local folder path 
            repo_name       --  the repo name

        returns:
            bundle_path     --  the path to the bundle file
    """
    print gh_repo_folder, repo_name
    if os.path.exists(gh_repo_folder):
        main_pwd = os.getcwd()
        os.chdir(gh_repo_folder)
        bundle_name = '{}.bundle'.format(repo_name)
        subprocess.check_call(['git','bundle','create', bundle_name, '--all'])
        bundle_path = '{}/{}'.format(gh_repo_folder,bundle_name)
        os.chdir(main_pwd)
    else:
        raise ValueError('Error creating bundle, directory does not exist: {}'.format(gh_repo_folder))
    return bundle_path

def upload_ia(gh_repo_folder, gh_repo_data, custom_meta=None):
    """Uploads the bundle to the Internet Archive.

        arguments:
                gh_repo_folder  -- path to the bundle
                gh_repo_data    -- repository metadata
                custom_meta     -- custom metadata

        returns:
                itemname        -- Internet Archive item identifier
                meta            -- the item metadata
                bundle_filename -- the git bundle filename
    """
    # formatting some dates string
    d = datetime.strptime(gh_repo_data['created_at'], '%Y-%m-%dT%H:%M:%SZ')
    pushed = datetime.strptime(gh_repo_data['pushed_at'], '%Y-%m-%dT%H:%M:%SZ')
    pushed_date = pushed.strftime('%Y-%m-%d_%H-%M-%S')
    raw_pushed_date = pushed.strftime('%Y-%m-%d %H:%M:%S')
    date = pushed.strftime('%Y-%m-%d')
    year = pushed.year

    # preparing some names
    repo_name = gh_repo_data['full_name'].replace('/','-')
    originalurl = gh_repo_data['html_url']
    bundle_filename = '{}_-_{}'.format(repo_name,pushed_date)

    # preparing some description
    description_footer = 'To restore the repository download the bundle <pre><code>wget https://archive.org/download/github.com-{0}/{0}.bundle</code></pre> and run: <pre><code> git clone {0}.bundle </code></pre>'.format(bundle_filename)
    description = '<br/> {0} <br/><br/> {1} <br/>{2}'.format(gh_repo_data['description'],get_description_from_readme(gh_repo_folder),description_footer)

    # preparing uploader metadata
    uploader_url = gh_repo_data['owner']['html_url']
    uploader_name = gh_repo_data['owner']['login']

    # let's grab the avatar too
    uploader_avatar_url = gh_repo_data['owner']['avatar_url']
    pic = requests.get(uploader_avatar_url, stream = True)
    uploader_avatar_path = '{}/cover.jpg'.format(gh_repo_folder)
    with open(uploader_avatar_path,'wb') as f:
        pic.raw.decode_content = True
        shutil.copyfileobj(pic.raw, f)

    # some Internet Archive Metadata
    collection = 'open_source_software'
    mediatype = 'software'
    subject = 'GitHub;code;software;git'

    uploader = '{} - {}'.format(__main_name__,__version__)

    description = u'{0} <br/><br/>Source: <a href="{1}">{2}</a><br/>Uploader: <a href="{3}">{4}</a><br/>Upload date: {5}'.format(description, originalurl, originalurl, uploader_url, uploader_name, date)

    ## Creating bundle file of  the  git repo
    try:
        bundle_file = create_bundle(gh_repo_folder, bundle_filename)
    except ValueError as err:
        print str(err)
        shutil.rmtree(gh_repo_folder)
        exit(1)

    # inizializing the internet archive item name
    # here we set the ia identifier
    itemname = '%s-%s_-_%s' % ('github.com', repo_name, pushed_date)
    title = '%s' % (itemname)

    #initializing the main metadata
    meta = dict(mediatype=mediatype, creator=uploader_name, collection=collection, title=title, year=year, date=date, \
           subject=subject, uploaded_with=uploader, originalurl=originalurl, pushed_date=raw_pushed_date, description=description)

    # override default metadata with any supplemental metadata provided.
    if custom_meta != None:
        meta.update(custom_meta)

    try:
        # upload the item to the Internet Archive
        print(("Creating item on Internet Archive: %s") % meta['title'])
        item = internetarchive.get_item(itemname)
        # checking if the item already exists:
        if not item.exists:
            print(("Uploading file to the internet archive: %s") % bundle_file)
            item.upload(bundle_file, metadata=meta, retries=9001, request_kwargs=dict(timeout=9001), delete=False)
            # upload the item to the Internet Archive
            print("Uploading avatar...")
            item.upload('{}/cover.jpg'.format(gh_repo_folder), retries=9001, request_kwargs=dict(timeout=9001), delete=True)
        else:
            print("\nSTOP: The same repository seems already archived.")
            print(("---->>  Archived repository URL: \n \thttps://archive.org/details/%s") % itemname)
            print("---->>  Archived git bundle file: \n \thttps://archive.org/download/{0}/{1}.bundle \n\n".format(itemname,bundle_filename))
            shutil.rmtree(gh_repo_folder)
            exit(0)

    except Exception as e:
        print str(e)
        shutil.rmtree(gh_repo_folder)
        exit(1)

    # return item identifier and metadata as output
    return itemname, meta, bundle_filename

def check_ia_credentials():
    """checks if the internet archive credentials are present.

        returns:
            exit(1) if there are no local credentialas.
    """
    filename = os.path.expanduser('~/.ia')
    filename2 = os.path.expanduser('~/.config/ia.ini')
    if not os.path.exists(filename) and not os.path.exists(filename2):
        msg = '\nWARNING - It looks like you need to configure your Internet Archive account!\n \
        for registation go to https://archive.org/account/login.createaccount.php\n'
        print(msg)
        try:
            noauth = subprocess.call(["ia", "configure"])
            if noauth:
                exit(1)
        except Exception as e:
            msg = 'Something went wrong trying to configure your internet archive account.\n Error - {}'.format(str(e))
            exit(1)



