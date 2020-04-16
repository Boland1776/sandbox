pipeline {
    agent {
        node {
            label 'v2_builder'
        }
    }
    stages {
        stage('Pull and Execute python script'){
            steps {
                echo "execute artifactory-cleaner.py"
                sh "python artifactory-cleaner.py bullhorn-activity-center-0.1"        
            }
        }
    }
}
