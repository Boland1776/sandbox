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
        stage('Delete repos'){
            input {
                message "Should we continue?"
                ok "Yes, we should."
 //               submitter "Chris"
 //               parameters {
 //                   string(name: 'PERSON', defaultValue: 'Mr Jenkins', description: 'Who should I say hello to?')
 //               }
            }
            environment {
                TEST_CREDS = credentials('my-predefined-ssh-creds')
            }
  //withCredentials([[$class: 'UsernamePasswordMultiBinding', credentialsId: 'c376347e-4245-49fc-be2c-b4aa0ddce81f',usernameVariable: 'NAME', passwordVariable: 'WORD']]) {
            steps {
                echo "Reading delete_list.txt"
                sh 'cat delete_list.txt'
 //               def j = 0
            }
        }
    }
}
