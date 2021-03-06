/**
 * The openstack-ardana-heat Jenkins Pipeline
 *
 * This job automates creating/deleting heat stacks.
 */

def ardana_lib = null

pipeline {

  options {
    // skip the default checkout, because we want to use a custom path
    skipDefaultCheckout()
    timestamps()
  }

  agent {
    node {
      label reuse_node ? reuse_node : "cloud-ardana-ci"
      // This job may also run asynchronously from its upstream job and may outlive it.
      // When that happens, the shared 'ardana_env' workspace may no longer be valid,
      // which is why this job needs a backup dedicated workspace (until a better mechanism
      // is used to manage a shared automation repository clone)
      customWorkspace "${JOB_NAME}-${BUILD_NUMBER}"
    }
  }

  stages {
    stage('Setup workspace') {
      steps {
        script {
          // Set this variable to be used by upstream builds
          env.blue_ocean_buildurl = env.RUN_DISPLAY_URL
          if (ardana_env == '') {
            error("Empty 'ardana_env' parameter value.")
          }
          if (heat_action == '') {
            error("Empty 'heat_action' parameter value.")
          }
          currentBuild.displayName = "#${BUILD_NUMBER}: ${heat_action} ${ardana_env}"
          // Use a shared workspace folder for all jobs running on the same
          // target 'ardana_env' cloud environment
          env.SHARED_WORKSPACE = sh (
            returnStdout: true,
            script: 'echo "$(dirname $WORKSPACE)/shared/${ardana_env}"'
          ).trim()
          if (reuse_node == '') {
            if (heat_action == 'create') {
              error("This job needs to be called by an upstream job to create a heat stack.")
            }
            // Resort to the backup dedicated workspace if this job is running asynchronously
            // from its upstream job
            env.SHARED_WORKSPACE = "$WORKSPACE"
            sh('''
              git clone $git_automation_repo --branch $git_automation_branch automation-git
              source automation-git/scripts/jenkins/ardana/jenkins-helper.sh
              ansible_playbook load-job-params.yml
            ''')
          }
          ardana_lib = load "$SHARED_WORKSPACE/automation-git/jenkins/ci.suse.de/pipelines/openstack-ardana.groovy"
        }
      }
    }
    stage('Delete heat stack') {
      steps {
        script {
          // Run the monitoring bits outside of the ECP-API lock, and lock the
          // ECP API only while actually deleting the stack
          ardana_lib.ansible_playbook('heat-stack', "-e heat_action=monitor")
          lock(resource: 'cloud-ECP-API') {
            ardana_lib.ansible_playbook('heat-stack', "-e heat_action=delete -e monitor_stack_after_delete=False")
          }
          ardana_lib.ansible_playbook('heat-stack', "-e heat_action=monitor")
        }
      }
    }
    stage('Create heat stack') {
      when {
        expression { heat_action == 'create' }
      }
      steps {
        script {
          lock(resource: 'cloud-ECP-API') {
            ardana_lib.ansible_playbook('heat-stack')
          }
        }
      }
    }
  }
  post {
    cleanup {
      cleanWs()
    }
  }
}
