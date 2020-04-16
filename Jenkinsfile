pipeline {
    agent brick
    stages {
        stage ('Pull and Execute python script'){
            println "execute artifactory-cleaner.py"
            sh "python artifactory-cleaner.py bullhorn-activity-center-0.1"        
        }
    }
}
