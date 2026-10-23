// CI/CD for afa-pipeline-core on the local Jenkins from jenkins/.
// Every commit: lint, type check, unit tests, Docker image, smoke test,
// then a Kubernetes Job in Minikube runs the new image on synthetic data.

pipeline {
    agent any

    options {
        timestamps()
        timeout(time: 30, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '20'))
        disableConcurrentBuilds()
    }

    environment {
        IMAGE = 'afa-pipeline-core'
        MINIKUBE_NODE = 'minikube'
        PIP_DISABLE_PIP_VERSION_CHECK = '1'
    }

    stages {
        stage('Set up Python') {
            steps {
                sh '''
                    python3 --version
                    python3 -m venv --clear .venv
                    .venv/bin/pip install --quiet -e ".[dev]"
                '''
            }
        }

        stage('Lint') {
            steps {
                sh '.venv/bin/ruff check . && .venv/bin/ruff format --check .'
            }
        }

        stage('Type check') {
            steps {
                sh '.venv/bin/mypy'
            }
        }

        stage('Unit tests') {
            steps {
                sh '''
                    .venv/bin/pytest \
                        --junitxml=reports/junit.xml \
                        --cov --cov-report=term \
                        --cov-report=xml:reports/coverage.xml \
                        --cov-report=html:reports/htmlcov \
                        --cov-fail-under=85
                '''
            }
            post {
                always {
                    junit 'reports/junit.xml'
                    recordCoverage(tools: [[parser: 'COBERTURA', pattern: 'reports/coverage.xml']])
                    archiveArtifacts artifacts: 'reports/**', allowEmptyArchive: true
                }
            }
        }

        stage('Build Docker image') {
            steps {
                script {
                    env.TAG = "${env.BUILD_NUMBER}-${env.GIT_COMMIT.take(7)}"
                }
                sh 'docker build --pull -t "$IMAGE:$TAG" -t "$IMAGE:latest" .'
            }
        }

        stage('Smoke test image') {
            steps {
                sh '''
                    docker run --rm --network none "$IMAGE:$TAG" --version
                    docker run --rm --network none "$IMAGE:$TAG" analyzers
                '''
            }
        }

        stage('Deploy to Minikube') {
            steps {
                sh '''
                    docker save "$IMAGE:$TAG" | docker exec -i "$MINIKUBE_NODE" ctr -n k8s.io images import -
                    kubectl delete job afa-analyze --ignore-not-found --wait=true
                    sed "s#image: IMAGE#image: $IMAGE:$TAG#" k8s/job.yaml | kubectl apply -f -
                    kubectl wait --for=condition=complete job/afa-analyze --timeout=180s
                '''
            }
            post {
                always {
                    sh '''
                        kubectl get job,pods -l app.kubernetes.io/name=afa-pipeline-core || true
                        kubectl logs job/afa-analyze || true
                    '''
                }
            }
        }
    }

    post {
        always {
            sh 'docker image ls "$IMAGE" || true'
            cleanWs()
        }
    }
}
