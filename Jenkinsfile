// OpenTofu GitOps pipeline: Jenkins job `tofu-plan-apply`, every run in a
// throwaway pod on the K3s cluster (jenkins-agents namespace).
//
// How it runs (job triggers + parameters live in the job definition,
// ansible/roles/jenkins/templates/jenkins-casc.yaml.j2):
//   - a push to master touching tofu/, this file or the report script
//     (Jenkins polls Gitea every ~2 min)  -> full plan, posted to Discord
//   - nightly                             -> full drift plan, posted only
//                                            if something changed
//   - "Build with Parameters", ACTION=plan  -> plan (optional TARGET)
//   - "Build with Parameters", ACTION=apply -> plan, a human approval
//     click in Jenkins, then `tofu apply` of exactly that saved plan.
//     TARGET is required: module.x[,module.y] or ALL.
//   - approved in Discord -> plan + apply, started by n8n
// Plan runs with changes go to n8n's "Tofu Plan Approval" workflow, which
// posts them to Discord with an Approve link (same pattern as the Diun
// update approvals). Clicking it makes n8n call this job's Generic Webhook
// Trigger (allowed only from n8n's IP) with the plan's fingerprint and
// commit. That run re-plans and applies only if master is still at that
// commit and the new plan's fingerprint is identical; otherwise it applies
// nothing and reports the mismatch. Nothing applies without a human click.
// Local `tofu` on devbox/workstation stays allowed.
//
// Secrets come from OpenBao secret/jenkins at runtime (withVault), are
// written only inside the pod, and are removed in `post`.
// Since OpenTofu 1.10 the official image is binary-only (no shell), so
// the pod is python:3-alpine and fetches a pinned, checksum-verified tofu.

def TOFU_VERSION = '1.12.6'
def TOFU_SHA256  = '50a6106fa4de523d09c87af85f3db1dd47535fc005727fdca6852146476b88ec'

def N8N_WEBHOOK = 'http://10.20.30.20:5678/webhook/tofu-plan'
def vaultConfig = [vaultUrl: 'http://10.20.30.22:8200', vaultCredentialId: 'vault-approle', engineVersion: 2]

def triggerKind() {
    if (currentBuild.getBuildCauses('org.jenkinsci.plugins.gwt.GenericCause')) { return 'approved' }
    if (currentBuild.getBuildCauses('hudson.model.Cause$UserIdCause')) { return 'manual' }
    if (currentBuild.getBuildCauses('hudson.triggers.TimerTrigger$TimerTriggerCause')) { return 'nightly' }
    return 'push'
}

// TARGET as typed in "Build with Parameters": "module.a,module.b" or ALL.
// A pasted "TARGET=..." prefix is tolerated (build #13 failed on exactly
// that); anything that isn't a resource address is refused up front.
def cleanTarget(String target) {
    return (target ?: '').trim().replaceFirst(/^(?i)TARGET\s*=\s*/, '')
}

def targetArgs(String target) {
    def t = cleanTarget(target)
    if (t == '' || t == 'ALL') { return '' }
    def items = t.split(',').collect { it.trim() }.findAll { it }
    def bad = items.findAll { !(it ==~ /[A-Za-z0-9_.\-\[\]"]+/) }
    if (bad) {
        error("TARGET has something that isn't a resource address: ${bad.join(', ')}. Use e.g. module.test_vm,module.test_lxc")
    }
    return items.collect { "-target=${it}" }.join(' ')
}

pipeline {
    agent {
        kubernetes {
            yaml '''
apiVersion: v1
kind: Pod
spec:
  nodeSelector:
    kubernetes.io/arch: amd64
  containers:
  - name: tofu
    image: python:3.13-alpine
    command: ["sleep"]
    args: ["99d"]
    resources:
      requests: {cpu: 250m, memory: 512Mi}
      limits: {memory: 2Gi}
'''
        }
    }

    options {
        disableConcurrentBuilds()
        timeout(time: 90, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '60'))
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
                script {
                    env.KIND = triggerKind()
                    env.DO_APPLY = (env.KIND == 'approved' || (env.KIND == 'manual' && params.ACTION == 'apply')) ? 'yes' : 'no'
                    if (env.KIND == 'manual' && env.DO_APPLY == 'yes' && !cleanTarget(params.TARGET)) {
                        error('ACTION=apply needs TARGET: module.<name>[,module.<name>] or ALL for the whole fleet.')
                    }
                    env.PLAN_TARGET = (env.KIND in ['manual', 'approved']) ? cleanTarget(params.TARGET) : ''
                    env.TARGET_ARGS = targetArgs(env.PLAN_TARGET)
                    env.COMMIT = sh(script: 'git rev-parse HEAD', returnStdout: true).trim()
                    env.SHORT_SHA = env.COMMIT.take(7)
                    env.N8N_WEBHOOK = N8N_WEBHOOK
                    env.FP_COMMIT = env.COMMIT
                    if (env.KIND == 'approved' && params.APPROVED_COMMIT != env.COMMIT) {
                        // Commits that don't touch tofu/ (docs, ansible) don't change what gets
                        // applied, so they don't void the approval. The fingerprint is then
                        // checked against the approved commit.
                        def tofuSame = params.APPROVED_COMMIT ==~ /[0-9a-f]{40}/ && sh(returnStatus: true,
                            script: "git cat-file -e ${params.APPROVED_COMMIT}^{commit} && git diff --quiet ${params.APPROVED_COMMIT} HEAD -- tofu/ scripts/tofu_plan_report.py") == 0
                        if (tofuSame) {
                            env.FP_COMMIT = params.APPROVED_COMMIT
                            echo "master moved ${params.APPROVED_COMMIT.take(7)} -> ${env.SHORT_SHA}, but nothing under tofu/ changed; approval still applies"
                        } else {
                            env.MISMATCH = "tofu/ changed since that plan (approved ${params.APPROVED_COMMIT?.take(7)}, now ${env.SHORT_SHA}); approve the newer plan instead"
                        }
                    }
                    currentBuild.description = "${env.KIND} ${env.DO_APPLY == 'yes' ? 'apply' : 'plan'} ${params.TARGET ?: ''} @${env.SHORT_SHA}"
                }
            }
        }

        stage('tofu init + plan') {
            steps {
                container('tofu') {
                    withVault(configuration: vaultConfig, vaultSecrets: [[
                        path: 'secret/jenkins',
                        secretValues: [
                            [envVar: 'TOFU_STATE_CONN_STR', vaultKey: 'jenkins_tofu_state_conn_str'],
                            [envVar: 'TOFU_KV_VARS_B64', vaultKey: 'jenkins_tofu_kv_vars_b64'],
                            [envVar: 'ANSIBLE_SSH_KEY_B64', vaultKey: 'jenkins_ansible_ssh_key_b64'],
                        ],
                    ]]) {
                        sh """
                            set -eu
                            cd /tmp
                            python3 -c 'import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' \
                              https://github.com/opentofu/opentofu/releases/download/v${TOFU_VERSION}/tofu_${TOFU_VERSION}_linux_amd64.tar.gz tofu.tar.gz
                            echo '${TOFU_SHA256}  tofu.tar.gz' | sha256sum -c -
                            tar -xzf tofu.tar.gz tofu
                            cp tofu /usr/local/bin/tofu && chmod 0755 /usr/local/bin/tofu
                            tofu version
                            cd "\$WORKSPACE"
                        """
                        // Single quotes: secrets are expanded by the shell, never by Groovy.
                        sh '''
                            set -eu
                            umask 077
                            echo "$TOFU_KV_VARS_B64" | base64 -d > tofu/vault_secret_ids.auto.tfvars
                            mkdir -p ~/.ssh
                            echo "$ANSIBLE_SSH_KEY_B64" | base64 -d > ~/.ssh/ansible_ed25519
                            cd tofu
                            tofu init -input=false -no-color -backend-config="conn_str=$TOFU_STATE_CONN_STR"
                        '''
                    }
                    timeout(time: 30, unit: 'MINUTES') {
                        script {
                            env.PLAN_RC = sh(returnStatus: true, script: '''
                                cd tofu
                                tofu plan -input=false -no-color -lock-timeout=5m -detailed-exitcode $TARGET_ARGS -out=deploy.tfplan > plan.txt 2>&1
                            ''').toString()
                        }
                    }
                    sh 'cat tofu/plan.txt'
                    script {
                        if (!(env.PLAN_RC in ['0', '2'])) { error("tofu plan failed (exit ${env.PLAN_RC})") }
                    }
                    sh 'cd tofu && tofu show -json deploy.tfplan > plan.json'
                }
            }
        }

        stage('Report') {
            steps {
                container('tofu') {
                    script {
                        def titles = [nightly: 'Nightly drift check', push: "Push ${env.SHORT_SHA}", approved: "Approved apply ${env.SHORT_SHA}"]
                        env.REPORT_TITLE = 'tofu plan: ' + (titles[env.KIND] ?: "Manual ${params.ACTION} ${env.PLAN_TARGET ?: 'full'}")
                    }
                    sh 'python3 scripts/tofu_plan_report.py tofu/plan.json --title "$REPORT_TITLE" --url "$BUILD_URL"'
                    script {
                        if (env.KIND == 'approved') {
                            def fp = sh(script: 'python3 scripts/tofu_plan_report.py tofu/plan.json --fingerprint --commit "$FP_COMMIT" --target "$PLAN_TARGET"', returnStdout: true).trim()
                            if (!env.MISMATCH && fp != params.APPROVED_FINGERPRINT) {
                                env.MISMATCH = 'the plan changed since it was approved (state or infrastructure moved); review the new plan'
                            }
                            if (env.MISMATCH) {
                                sh 'python3 scripts/tofu_plan_report.py tofu/plan.json --title "Not applied: $MISMATCH" --url "$BUILD_URL" --post "$N8N_WEBHOOK" --event result --kind approved --commit "$COMMIT" --target "$PLAN_TARGET" --status mismatch'
                                env.DO_APPLY = 'no'
                                unstable("Not applying: ${env.MISMATCH}")
                                currentBuild.description = "approved apply NOT applied: plan mismatch @${env.SHORT_SHA}"
                            }
                        } else if (!(env.KIND == 'manual' && params.ACTION == 'apply')) {
                            // Manual apply runs use the Jenkins input gate below instead.
                            env.REPORT_ONLY = env.KIND == 'nightly' ? '--only-if-changes' : ''
                            sh 'python3 scripts/tofu_plan_report.py tofu/plan.json --title "$REPORT_TITLE" --url "$BUILD_URL" --post "$N8N_WEBHOOK" --event plan --kind "$KIND" --commit "$COMMIT" --target "$PLAN_TARGET" $REPORT_ONLY'
                        }
                    }
                }
            }
        }

        stage('Approval') {
            when { expression { env.KIND == 'manual' && env.DO_APPLY == 'yes' && env.PLAN_RC == '2' } }
            steps {
                script {
                    def summary = ''
                    container('tofu') {
                        summary = sh(script: 'python3 scripts/tofu_plan_report.py tofu/plan.json --title "Plan to apply"', returnStdout: true).trim()
                    }
                    timeout(time: 60, unit: 'MINUTES') {
                        input message: "${summary}\n\nFull plan is in the 'tofu init + plan' stage log. Apply exactly this plan?", ok: 'Apply'
                    }
                }
            }
        }

        stage('tofu apply') {
            when { expression { env.DO_APPLY == 'yes' && env.PLAN_RC == '2' } }
            steps {
                container('tofu') {
                    sh 'cd tofu && tofu apply -input=false -no-color deploy.tfplan'
                }
            }
        }

        // Every VM/LXC this apply created gets scripts/host_check.py: keys,
        // DNS, inventory, metrics, logs, PatchMon, backups. A brand-new host
        // has not had its Ansible playbook yet, so failures here are the
        // to-do list for finishing it, posted to Discord; they never fail
        // the build.
        stage('Check new hosts') {
            when { expression { env.DO_APPLY == 'yes' && env.PLAN_RC == '2' } }
            steps {
                container('tofu') {
                    sh '''
                        apk add --no-cache -q openssh-client >/dev/null
                        python3 scripts/host_check.py --from-plan tofu/plan.json --wait-ssh 300 \
                          --post "$N8N_WEBHOOK" --commit "$COMMIT" --url "$BUILD_URL" || true
                    '''
                }
            }
        }
    }

    post {
        success {
            script {
                if (env.KIND == 'approved') {
                    container('tofu') {
                        env.RESULT_STATUS = env.PLAN_RC == '2' ? 'applied' : 'nothing'
                        sh 'python3 scripts/tofu_plan_report.py tofu/plan.json --title "tofu apply" --url "$BUILD_URL" --post "$N8N_WEBHOOK" --event result --kind approved --commit "$COMMIT" --target "$PLAN_TARGET" --status "$RESULT_STATUS"'
                    }
                }
            }
        }
        failure {
            container('tofu') {
                sh 'python3 scripts/tofu_plan_report.py - --title "tofu pipeline failed" --url "$BUILD_URL" --post "$N8N_WEBHOOK" --event result --kind "${KIND:-unknown}" --commit "${COMMIT:-}" --target "${PLAN_TARGET:-}" --status failed || true'
            }
        }
        cleanup {
            container('tofu') {
                sh 'rm -f tofu/vault_secret_ids.auto.tfvars ~/.ssh/ansible_ed25519 tofu/deploy.tfplan tofu/plan.json'
            }
        }
    }
}
