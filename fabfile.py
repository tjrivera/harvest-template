from __future__ import print_function, with_statement
import os
import json
import etcd
from functools import wraps
from fabric.api import *
from fabric.colors import red, yellow, white
from fabric.contrib.console import confirm
from fabric.contrib.files import exists


__doc__ = """\
Before using this fabfile, you must create a .fabhosts in your project
directory. It is a JSON file with the following structure:

    {
        "_": {
            "host_string": "example.com",
            "path": "~/sites/project-env/project",
            "repo_url": "git@github.com/bruth/project.git",
            "nginx_conf_dir": "~/etc/nginx/conf.d",
            "supervisor_conf_dir": "~/etc/supervisor.d"
        },
        "production": {},
        "development": {
            "path": "~/sites/project-dev-env/project"
        },
        "staging": {
            "path": "~/sites/project-stage-env/project"
        }
    }

The "_" entry acts as the default/fallback for the other host
settings, so you only have to define the host-specific settings.
The below settings are required:

* `host_string` - hostname or IP address of the host server
* `path` - path to the deployed project *within* it's virtual environment
* `repo_url` - URL to project git repository
* `nginx_conf_dir` - path to host's nginx conf.d directory
* `supervisor_conf_dir` - path to host's supervisor

Note, additional settings can be defined and will be set on the `env`
object, but the above settings are required at a minimum.
"""

# A few setup steps and environment checks
curdir = os.path.dirname(os.path.abspath(__file__))
hosts_file = os.path.join(curdir, '.fabhosts')

# Check for the .fabhosts file
if not os.path.exists(hosts_file):
    abort(white(__doc__))

base_settings = {
    'host_string': '',
}

required_settings = ['host_string']


def get_hosts_settings():
    # Load all the host settings
    hosts = json.loads(open(hosts_file).read())

    # Pop the default settings
    default_settings = hosts.pop('_', {})

    # Pre-populated defaults
    for host in hosts:
        base = base_settings.copy()
        base.update(default_settings)
        base.update(hosts[host])
        hosts[host] = base

    if not env.hosts:
        abort(red('Error: At least one host must be specified'))

    # Validate all hosts have an entry in the .hosts file
    for target in env.hosts:
        if target not in hosts:
            abort(red('Error: No settings have been defined for the "{}" host'.format(target)))
        settings = hosts[target]
        for key in required_settings:
            if not settings[key]:
                abort(red('Error: The setting "{}" is not defined for "{}" host'.format(key, target)))
    return hosts


def host_context(func):
    "Sets the context of the setting to the current host"
    @wraps(func)
    def decorator(*args, **kwargs):
        hosts = get_hosts_settings()
        with settings(**hosts[env.host]):
            return func(*args, **kwargs)
    return decorator

@host_context
def get_application_config(**kwargs):
    host = env.host
    client = etcd.Client(host=env.etcd_host)

    # Retrieve Environment Variables from etcd service
    env_vars = client.read('/ehb-service/config/%s' % host, recursive=True)
    env_str = ''
    for child in env_vars.children:
        key = child.key.split('/')[-1]
        if key in kwargs.keys():
            if kwargs[key]==None:
                pass
            else:
                env_str += '-e %s=%s ' % (key,kwargs[key])
        else:
            env_str += '-e %s=%s ' % (key,child.value)
    env_str += '-e GIT_BRANCH=%s' % (env.git_branch)
    return env_str

@host_context
def setup_env():
    "Sets up the initial environment."
    parent, project = os.path.split(env.path)
    if not exists(parent):
        run('mkdir -p {}'.format(parent))
        run('virtualenv {}'.format(parent))

    with cd(parent):
        if not exists(project):
            run('git clone {repo_url} {project}'.format(project=project, **env))
            with cd(project):
                run('git checkout {git_branch}'.format(**env))
                run('git pull origin {git_branch}'.format(**env))
        else:
            with cd(project):
                run('git checkout {git_branch}'.format(**env))
                run('git pull origin {git_branch}'.format(**env))


@host_context
def push_to_repo():
    with cd(env.path):
        git_hash = run('git rev-parse HEAD')[0:7]
    run('docker tag ehb-service-%s:%s %s/ehb-service-%s' % (env.git_branch, git_hash, env.docker_registry, env.git_branch))
    run('docker push %s/ehb-service-%s' % (env.docker_registry, env.git_branch))
    run('docker rmi %s/ehb-service-%s' % (env.docker_registry, env.git_branch))

@host_context
def test_container():
    with cd(env.path):
        git_hash = run('git rev-parse HEAD')[0:7]
    container = run('docker run -t %s ehb-service-%s:%s  /bin/sh -e /usr/local/bin/test' % (get_application_config(),env.git_branch, git_hash))

@host_context
def build_container():
    # Get ID of existing images:
    # docker images -q ehb-service-development
    # Get processes running on old container:

    with cd(env.path):
        setup_env()
        git_hash = run('git rev-parse HEAD')[0:7]
    run('docker build -rm -t ehb-service-%s:%s %s' % (env.git_branch, git_hash, env.path))

# @host_context
# def deploy_container():
#     # Run the container
#     container = run('docker run -d -p :8000 %s ehb-service-%s  /bin/sh -e /usr/local/bin/deploy' % (get_application_config(), env.host))

#     # Retrieve information about which port the container is running on.
#     container_info = json.loads(run('docker inspect %s' % container, quiet=True))[0]
#     nginx_conf = '''
#     location /ehb-docker {
#         uwsgi_param SCRIPT_NAME /ehb-docker;
#         uwsgi_pass 127.0.0.1:%s;
#         uwsgi_modifier1 30;
#         uwsgi_read_timeout 120;
#         include uwsgi_params;
#     }
#     ''' % container_info['NetworkSettings']['Ports']['8000/tcp'][0]['HostPort']
#     sudo('cat >/etc/nginx/conf.d/ehb-docker-%s.conf <<EOL %s\nEOL' % (env.host,nginx_conf))
#     reload_nginx()

@host_context
def pull_repo():
    with cd(env.path):
        run('docker pull %s/ehb-service-%s' % (env.docker_registry, env.git_branch))

@host_context
def run_container():
    # with cd(env.path):
        #setup_env()
    pull_repo()
    container = run('docker run -d -p :8000 %s %s/ehb-service-%s:latest  /bin/sh -e /usr/local/bin/run' % (get_application_config(FORCE_SCRIPT_NAME=''),env.docker_registry, env.git_branch))
    container_info = json.loads(run('docker inspect %s' % container, quiet=True))[0]
    print(red('Now running at http://%s:%s ' % (env.host_string,container_info['NetworkSettings']['Ports']['8000/tcp'][0]['HostPort'])))

@host_context
def reload_nginx():
    sudo('/etc/init.d/nginx reload')

@host_context
def integration_test():
    # Production copy down
    pass
    # Test on production data
    container = run('')
    # Run on
    pass
