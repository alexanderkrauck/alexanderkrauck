// GitHub Projects API Integration
class GitHubProjectsLoader {
    constructor(username = 'alexanderkrauck') {
        this.username = username;
        this.apiUrl = `https://api.github.com/users/${username}/repos`;
        this.projects = [];
    }

    async loadProjects() {
        try {
            const response = await fetch(`${this.apiUrl}?sort=updated&per_page=50`);
            if (!response.ok) throw new Error('Failed to fetch repositories');
            
            const repos = await response.json();
            this.projects = repos
                .filter(repo => !repo.fork && !repo.private) // Only original public repos
                .map(repo => ({
                    name: repo.name,
                    description: repo.description || 'No description available',
                    url: repo.html_url,
                    language: repo.language,
                    stars: repo.stargazers_count,
                    forks: repo.forks_count,
                    updated: new Date(repo.updated_at),
                    topics: repo.topics || [],
                    size: repo.size
                }))
                .sort((a, b) => b.updated - a.updated); // Sort by most recently updated
            
            return this.projects;
        } catch (error) {
            console.error('Error loading GitHub projects:', error);
            return this.getFallbackProjects();
        }
    }

    getFallbackProjects() {
        // Fallback data in case GitHub API is unavailable
        return [
            {
                name: 'InfiniLead',
                description: 'Cyberpunk AI Portfolio Website with Interactive Games',
                url: '#',
                language: 'JavaScript',
                stars: 0,
                forks: 0,
                updated: new Date(),
                topics: ['cyberpunk', 'portfolio', 'games', 'ai'],
                size: 1024
            },
            {
                name: 'AI-Research-Framework',
                description: 'Graph Neural Networks and Uncertainty Quantification Research',
                url: '#',
                language: 'Python',
                stars: 0,
                forks: 0,
                updated: new Date(Date.now() - 86400000),
                topics: ['ai', 'research', 'gnn', 'machine-learning'],
                size: 2048
            }
        ];
    }

    getLanguageColor(language) {
        const colors = {
            'JavaScript': '#f1e05a',
            'Python': '#3572A5',
            'TypeScript': '#2b7489',
            'Java': '#b07219',
            'C++': '#f34b7d',
            'C': '#555555',
            'HTML': '#e34c26',
            'CSS': '#563d7c',
            'Go': '#00ADD8',
            'Rust': '#dea584',
            'Swift': '#ffac45',
            'Kotlin': '#F18E33',
            'PHP': '#4F5D95',
            'Ruby': '#701516',
            'Shell': '#89e051',
            'Jupyter Notebook': '#DA5B0B'
        };
        return colors[language] || '#00ff88';
    }

    renderProjects(containerId) {
        const container = document.getElementById(containerId);
        if (!container) return;

        container.innerHTML = this.projects.map(project => `
            <div class="project-card" data-language="${project.language || 'Unknown'}">
                <div class="project-header">
                    <h3 class="project-name">
                        <a href="${project.url}" target="_blank" rel="noopener">
                            ${project.name}
                        </a>
                    </h3>
                    <div class="project-stats">
                        <span class="stat">
                            <i class="icon-star">⭐</i>
                            ${project.stars}
                        </span>
                        <span class="stat">
                            <i class="icon-fork">🔀</i>
                            ${project.forks}
                        </span>
                    </div>
                </div>
                
                <p class="project-description">${project.description}</p>
                
                <div class="project-footer">
                    <div class="project-meta">
                        ${project.language ? `
                            <span class="language" style="color: ${this.getLanguageColor(project.language)}">
                                <span class="language-dot" style="background-color: ${this.getLanguageColor(project.language)}"></span>
                                ${project.language}
                            </span>
                        ` : ''}
                        <span class="updated">
                            Updated ${this.formatDate(project.updated)}
                        </span>
                    </div>
                    
                    ${project.topics.length > 0 ? `
                        <div class="project-topics">
                            ${project.topics.slice(0, 3).map(topic => 
                                `<span class="topic-tag">${topic}</span>`
                            ).join('')}
                        </div>
                    ` : ''}
                </div>
            </div>
        `).join('');

        this.addProjectCardAnimations();
    }

    formatDate(date) {
        const now = new Date();
        const diffTime = Math.abs(now - date);
        const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
        
        if (diffDays === 1) return 'yesterday';
        if (diffDays < 7) return `${diffDays} days ago`;
        if (diffDays < 30) return `${Math.ceil(diffDays / 7)} weeks ago`;
        if (diffDays < 365) return `${Math.ceil(diffDays / 30)} months ago`;
        return `${Math.ceil(diffDays / 365)} years ago`;
    }

    addProjectCardAnimations() {
        const cards = document.querySelectorAll('.project-card');
        cards.forEach((card, index) => {
            card.style.animationDelay = `${index * 0.1}s`;
            
            card.addEventListener('mouseenter', () => {
                card.style.transform = 'translateY(-10px) scale(1.02)';
                card.style.boxShadow = '0 20px 40px rgba(0,255,136,0.3)';
            });
            
            card.addEventListener('mouseleave', () => {
                card.style.transform = 'translateY(0) scale(1)';
                card.style.boxShadow = '0 10px 20px rgba(0,255,136,0.1)';
            });
        });
    }

    async init(containerId) {
        // Show loading state
        const container = document.getElementById(containerId);
        if (container) {
            container.innerHTML = `
                <div class="loading-projects">
                    <div class="loading-spinner"></div>
                    <div class="loading-text">ACCESSING GITHUB REPOSITORIES...</div>
                </div>
            `;
        }

        // Load and render projects
        await this.loadProjects();
        this.renderProjects(containerId);
    }
}

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    const githubLoader = new GitHubProjectsLoader();
    githubLoader.init('github-projects');
});

// Export for use in other modules
window.GitHubProjectsLoader = GitHubProjectsLoader; 