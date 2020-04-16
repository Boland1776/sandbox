pipeline {
    node("v2_builder"){
        stage ('Execute python script'){
            println "execute artifactory-cleaner.py"
            sh "python artifactory-cleaner.py bullhorn-activity-center-0.1"        
        }
    }
}
